"""Classes for enabling ha in Openstack charms with the reactive framework"""

import ipaddress

import charmhelpers.contrib.network.ip as ip
from charmhelpers.core.hookenv import unit_private_ip, config
from relations.hacluster.common import CRM
from relations.hacluster.common import ResourceDescriptor

VIP_KEY = "vip"
CIDR_KEY = "vip_cidr"
IFACE_KEY = "vip_iface"

"""Configure ha resources with:
@when('ha.connected')
def cluster_connected(hacluster):
    ha.configure_ha_resources(hacluster, 'designate', ha_resources=['vips', 'haproxy'])

TODO Proper docs to follow
"""

def configure_ha_resources(hacluster, service_name, ha_resources=None):
    user_config = config()

    RESOURCE_TYPES = {
        'vips': configure_vips,
        'haproxy': configure_haproxy,
    }
    if not ha_resources:
        return
    resources = CRM()
    for res_type in ha_resources:
        resources = RESOURCE_TYPES[res_type](resources, service_name)
    # TODO Remove hardcoded multicast port
    hacluster.bind_on(iface=user_config[IFACE_KEY], mcastport=4440)
    hacluster.manage_resources(resources)

def configure_vips(_resources, service_name):
    user_config = config()

    for vip in user_config.get(VIP_KEY, []).split():
        iface = (ip.get_iface_for_address(vip) or
                 config(IFACE_KEY)) 
        netmask = (ip.get_netmask_for_address(vip) or
                   config(CIDR_KEY))
        if iface is not None:
            _resources.add(
                VirtualIP(
                    service_name,
                    vip,
                    nic=iface,
                    cidr=netmask,))
    return _resources

def configure_haproxy(_resources, service_name):
    _resources.add(
        InitService(
            service_name,
            'haproxy',))
    return _resources

class InitService(ResourceDescriptor):
    def __init__(self, service_name, init_service_name):
        self.service_name = service_name
        self.init_service_name = init_service_name

    def configure_resource(self, crm):
        res_key = 'res_{}_{}'.format(
            self.service_name.replace('-', '_'),
            self.init_service_name.replace('-', '_'))
        clone_key = 'cl_{}'.format(res_key)
        res_type = 'lsb:{}'.format(self.init_service_name)
        crm.primitive(res_key, res_type, params='op monitor interval="5s"')
        crm.init_services(self.init_service_name)
        crm.clone(clone_key, res_key)

class VirtualIP(ResourceDescriptor):
    def __init__(self, service_name, vip, nic=None, cidr=None):
        self.service_name = service_name
        self.vip = vip
        self.nic = nic
        self.cidr = cidr

    def configure_resource(self, crm):
        vip_key = 'res_{}_{}_vip'.format(self.service_name, self.nic)
        ipaddr = ipaddress.ip_address(self.vip)
        if isinstance(ipaddr, ipaddress.IPv4Address):
            res_type = 'ocf:heartbeat:IPaddr2'
            res_params = 'ip="{}"'.format(self.vip)
        else:
            res_type = 'ocf:heartbeat:IPv6addr'
            res_params = 'ipv6addr="{}"'.format(self.vip)

        if self.nic:
            res_params = '{} nic="{}"'.format(res_params, self.nic)
        if self.cidr:
            res_params = '{} cidr_netmask="{}"'.format(res_params, self.cidr)
        crm.primitive(vip_key, res_type, params=res_params)
