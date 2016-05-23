"""Classes for enabling ha in Openstack charms with the reactive framework"""

import ipaddress
import relations.hacluster.common


"""Configure ha resources with:
@when('ha.connected')
def cluster_connected(hacluster):
    charm = DesignateCharmFactory.charm()
    charm.configure_ha_resources(hacluster)

TODO Proper docs to follow
"""


class InitService(relations.hacluster.common.ResourceDescriptor):
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


class VirtualIP(relations.hacluster.common.ResourceDescriptor):
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
