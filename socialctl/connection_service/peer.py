"""Trust the edge's overwritten X-Real-IP only from explicitly configured peers."""
from ipaddress import ip_address, ip_network


def client_ip(request, settings):
    peer = request.client.host if request.client else 'unknown'
    try:
        address = ip_address(peer)
        if any(address in ip_network(cidr) for cidr in settings.trusted_proxy_cidrs):
            return str(ip_address(request.headers.get('x-real-ip', '')))
    except ValueError:
        pass
    return peer
