"""Classes to support writing re-usable charms in the reactive framework"""

from __future__ import absolute_import

import subprocess
import os
from contextlib import contextmanager
from collections import OrderedDict

from charmhelpers.contrib.openstack.utils import (
    configure_installation_source,
)
from charmhelpers.core.host import path_hash, service_restart, pwgen
from charmhelpers.core.hookenv import config, status_set, relation_ids
from charmhelpers.fetch import (
    apt_install,
    apt_update,
    filter_installed_packages,
)
from charmhelpers.contrib.openstack.templating import get_loader
from charmhelpers.core.templating import render
from charmhelpers.core.hookenv import leader_get, leader_set
from charms.reactive.bus import set_state, remove_state, get_state

from charm.openstack.ip import PUBLIC, INTERNAL, ADMIN, canonical_url
import charmhelpers.contrib.network.ip as ip
import charm.openstack.ha as ha
from relations.hacluster.common import CRM
import relations.openstack_ha.peers as ha_peers

VIP_KEY = "vip"
CIDR_KEY = "vip_cidr"
IFACE_KEY = "vip_iface"


class OpenStackCharm(object):
    """
    Base class for all OpenStack Charm classes;
    encapulates general OpenStack charm payload operations
    """

    name = 'charmname'

    base_packages = []
    """Packages to install unconditionally"""

    api_ports = {}
    """
    Dictionary mapping services to ports for public, admin and
    internal endpoints
    """

    service_type = None
    """Keystone endpoint type"""

    default_service = None
    """Default service for the charm"""

    base_restart_map = {}
    """Map of services which must always be restarted when corresponding
       configuration file changes
    """
    sync_cmd = []
    services = []
    ha_resources = []
    adapters_class = None
    HAPROXY_CONF = '/etc/haproxy/haproxy.cfg'

    def __init__(self, interfaces=None):
        self.config = config()
        # XXX It's not always liberty!
        self.release = 'liberty'
        if interfaces and self.adapters_class:
            self.adapter_instance = self.adapters_class(interfaces)
        self.set_haproxy_stat_password()

    def enable_haproxy(self):
        """Determine if haproxy is fronting the services

        @return True if haproxy is fronting the service"""
        return 'haproxy' in self.ha_resources

    @property
    def packages(self):
        """List of packages to be installed

        @return ['pkg1', 'pkg2', ...]
        """
        _packages = []
        _packages.extend(self.base_packages)
        if self.enable_haproxy():
            _packages.append('haproxy')
        return _packages

    @property
    def restart_map(self):
        """Map of services to be restarted if a file changes

        @return {
                    'file1': ['svc1', 'svc3'],
                    'file2': ['svc2', 'svc3'],
                    ...
                }
        """
        _restart_map = self.base_restart_map.copy()
        if self.enable_haproxy():
            _restart_map[self.HAPROXY_CONF] = ['haproxy']
        return _restart_map

    def install(self):
        """
        Install packages related to this charm based on
        contents of packages attribute.
        """
        packages = filter_installed_packages(self.packages)
        if packages:
            status_set('maintenance', 'Installing packages')
            apt_install(packages, fatal=True)
        self.set_state('{}-installed'.format(self.name))

    def set_state(self, state, value=None):
        set_state(state, value)

    def remove_state(self, state):
        remove_state(state)

    def api_port(self, service, endpoint_type=PUBLIC):
        """
        Determine the API port for a particular endpoint type
        """
        return self.api_ports[service][endpoint_type]

    def configure_source(self):
        """Configure installation source"""
        configure_installation_source(self.config['openstack-origin'])
        apt_update(fatal=True)

    @property
    def region(self):
        """OpenStack Region"""
        return self.config['region']

    @property
    def public_url(self):
        """Public Endpoint URL"""
        return "{}:{}".format(canonical_url(PUBLIC),
                              self.api_port(self.default_service,
                                            PUBLIC))

    @property
    def admin_url(self):
        """Admin Endpoint URL"""
        return "{}:{}".format(canonical_url(ADMIN),
                              self.api_port(self.default_service,
                                            ADMIN))

    @property
    def internal_url(self):
        """Internal Endpoint URL"""
        return "{}:{}".format(canonical_url(INTERNAL),
                              self.api_port(self.default_service,
                                            INTERNAL))

    @contextmanager
    def restart_on_change(self):
        checksums = {path: path_hash(path) for path in self.restart_map.keys()}
        yield
        restarts = []
        for path in self.restart_map:
            if path_hash(path) != checksums[path]:
                restarts += self.restart_map[path]
        services_list = list(OrderedDict.fromkeys(restarts).keys())
        for service_name in services_list:
            service_restart(service_name)

    def render_all_configs(self):
        self.render_configs(self.restart_map.keys())

    def render_configs(self, configs):
        with self.restart_on_change():
            for conf in configs:
                render(source=os.path.basename(conf),
                       template_loader=get_loader('templates/', self.release),
                       target=conf,
                       context=self.adapter_instance)

    def restart_all(self):
        for svc in self.services:
            service_restart(svc)

    def db_sync(self):
        sync_done = leader_get(attribute='db-sync-done')
        if not sync_done:
            subprocess.check_call(self.sync_cmd)
            leader_set({'db-sync-done': True})
            # Restart services immediatly after db sync as
            # render_domain_config needs a working system
            self.restart_all()

    def configure_ha_resources(self, hacluster):
        """Inform the ha subordinate about each service it should manage. The
        child class specifies the services via self.ha_resources

        @param hacluster interface
        """
        RESOURCE_TYPES = {
            'vips': self._add_ha_vips_config,
            'haproxy': self._add_ha_haproxy_config,
        }
        self.resources = CRM()
        if not self.ha_resources:
            return
        for res_type in self.ha_resources:
            RESOURCE_TYPES[res_type]()
        # TODO Remove hardcoded multicast port
        hacluster.bind_on(iface=self.config[IFACE_KEY], mcastport=4440)
        hacluster.manage_resources(self.resources)

    def _add_ha_vips_config(self):
        """Add a VirtualIP object for each user specified vip to self.resources
        """
        for vip in self.config.get(VIP_KEY, []).split():
            iface = (ip.get_iface_for_address(vip) or
                     self.config(IFACE_KEY))
            netmask = (ip.get_netmask_for_address(vip) or
                       self.config(CIDR_KEY))
            if iface is not None:
                self.resources.add(
                    ha.VirtualIP(
                        self.name,
                        vip,
                        nic=iface,
                        cidr=netmask,))

    def _add_ha_haproxy_config(self):
        """Add a InitService object for haproxy to self.resources
        """
        self.resources.add(
            ha.InitService(
                self.name,
                'haproxy',))

    def set_haproxy_stat_password(self):
        """Set a stats password for accessing haproxy statistics"""
        if not get_state('haproxy.stat.password'):
            set_state('haproxy.stat.password', pwgen(32))


class OpenStackCharmFactory(object):

    releases = {}
    """
    Dictionary mapping OpenStack releases to their associated
    Charm class for this charm
    """

    first_release = "icehouse"
    """
    First OpenStack release which this factory supports Charms for
    """

    @classmethod
    def charm(cls, release=None, interfaces=None):
        """
        Get an instance of the right charm for the configured OpenStack series

        If the cluster relation exists add the cluster interface. It is
        forecfully added here as the interface is needed even if there is only
        one unit in the service. If only one unit exists the cluster hooks
        never fire.
        """
        if relation_ids('cluster'):
            cluster_interface = ha_peers.OpenstackHAPeers('cluster')
            if interfaces:
                interfaces.append(cluster_interface)
            else:
                interfaces = [cluster_interface]
        if release and release in cls.releases:
            return cls.releases[release](interfaces=interfaces)
        else:
            return cls.releases[cls.first_release](interfaces=interfaces)
