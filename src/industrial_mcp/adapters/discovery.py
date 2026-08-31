"""Find SiloScan modules that moved.

The bench module takes a new DHCP lease on most reboots — three different
addresses in three restarts — so anything that writes the IP into a file is
stale by the next power cycle.

Two ways out, cheapest first:

1. **Ask for it by name.** Firmware 0.5.0 and later publish
   ``<device_id>.local`` over mDNS, so the address stops being the identity.
   This is one DNS lookup and it is what should normally work.
2. **Sweep the subnet.** Only when the name does not resolve — an older
   build, or a host whose resolver has no mDNS. Probes ``/api/status`` on
   every address and keeps the ones that answer as the module we want.

A scan is bounded to private address space on purpose. This code path can be
reached from a model-facing tool, and sweeping arbitrary public ranges on
someone's say-so is not a thing this repo should make easy.
"""

from __future__ import annotations

import ipaddress
import socket
from concurrent.futures import ThreadPoolExecutor
from typing import Any

import httpx

# Short: a module on the LAN answers /api/status in single-digit milliseconds.
# Anything slower is almost certainly a host that is not it.
_PROBE_TIMEOUT_S = 0.6
_MAX_WORKERS = 32


def local_subnet() -> str | None:
    """The /24 this machine sits on, or None if that cannot be determined."""
    try:
        # No packet is sent; this just asks the routing table which local
        # address would be used to reach a public one.
        sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        try:
            sock.connect(("8.8.8.8", 80))
            local_ip = sock.getsockname()[0]
        finally:
            sock.close()
    except OSError:
        return None
    return str(ipaddress.ip_network(f"{local_ip}/24", strict=False))


def _probe(host: str) -> dict[str, Any] | None:
    """One GET /api/status. Returns the module's identity, or None."""
    try:
        response = httpx.get(f"http://{host}/api/status", timeout=_PROBE_TIMEOUT_S)
        response.raise_for_status()
        status = response.json()
    except (httpx.HTTPError, ValueError):
        return None
    # Something answered — but plenty of things answer HTTP. Only a SiloScan
    # module reports these fields, and requiring them keeps a router's admin
    # page from being reported as a thermometry device.
    if not isinstance(status, dict) or "device_id" not in status or "fw" not in status:
        return None
    return {
        "host": host,
        "device_id": status.get("device_id"),
        "fw": status.get("fw"),
        "silo": status.get("silo"),
        "uptime_s": status.get("uptime_s"),
    }


def resolve_by_name(device_id: str) -> dict[str, Any] | None:
    """Try ``<device_id>.local`` — the cheap path, firmware 0.5.0 and later."""
    found = _probe(f"{device_id}.local")
    if found and found.get("device_id") == device_id:
        # Report the name, not the address it resolved to: the name is the
        # part that stays true across the next DHCP lease.
        found["host"] = f"{device_id}.local"
        found["found_by"] = "mdns"
        return found
    return None


def scan_subnet(subnet: str | None = None) -> list[dict[str, Any]]:
    """Probe every address in ``subnet`` and return the modules that answer.

    Refuses anything outside private address space.
    """
    subnet = subnet or local_subnet()
    if subnet is None:
        raise ValueError("could not determine the local subnet; pass one explicitly")

    network = ipaddress.ip_network(subnet, strict=False)
    if not network.is_private:
        raise ValueError(
            f"{subnet} is not a private network — scanning is limited to RFC1918 space"
        )
    if network.num_addresses > 1024:
        raise ValueError(
            f"{subnet} has {network.num_addresses} addresses; use a /22 or smaller"
        )

    hosts = [str(ip) for ip in network.hosts()]
    with ThreadPoolExecutor(max_workers=_MAX_WORKERS) as pool:
        results = pool.map(_probe, hosts)

    found = [r for r in results if r is not None]
    for entry in found:
        entry["found_by"] = "scan"
    return found


def find_device(device_id: str, subnet: str | None = None) -> str | None:
    """Locate one module by id. Returns a host string, or None.

    Name first, sweep second — a lookup that fails costs milliseconds, a
    sweep costs a couple of seconds.
    """
    by_name = resolve_by_name(device_id)
    if by_name:
        return by_name["host"]

    try:
        candidates = scan_subnet(subnet)
    except ValueError:
        return None
    for entry in candidates:
        if entry.get("device_id") == device_id:
            return entry["host"]
    return None
