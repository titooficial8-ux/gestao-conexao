"""Evita 'getaddrinfo failed' intermitente: se o DNS da rede falhar, usa o último IP
conhecido do host ou consulta DNS público por HTTPS (1.1.1.1 / 8.8.8.8, direto por IP)."""
from __future__ import annotations

import json
import os
import socket
import ssl
import time
import urllib.request
from pathlib import Path

_original = socket.getaddrinfo
_cache: dict[str, tuple[list[str], float]] = {}
_arquivo = Path(os.getenv("GESTAO_DNS_CACHE", Path(__file__).resolve().parent.parent / "instance" / "dns_cache.json"))
_instalado = False


def _carregar():
    try:
        for host, ips in json.loads(_arquivo.read_text(encoding="utf-8")).items():
            _cache.setdefault(host, (ips, 0.0))
    except Exception:
        pass


def _salvar():
    try:
        _arquivo.parent.mkdir(parents=True, exist_ok=True)
        _arquivo.write_text(json.dumps({h: v[0] for h, v in _cache.items()}), encoding="utf-8")
    except Exception:
        pass


def _doh(host: str) -> list[str]:
    ctx = ssl.create_default_context()
    for servidor in ("1.1.1.1", "8.8.8.8"):
        try:
            req = urllib.request.Request(
                f"https://{servidor}/dns-query?name={host}&type=A",
                headers={"accept": "application/dns-json"})
            with urllib.request.urlopen(req, timeout=5, context=ctx) as r:
                dados = json.loads(r.read())
            ips = [a["data"] for a in dados.get("Answer", []) if a.get("type") == 1]
            if ips:
                return ips
        except Exception:
            continue
    return []


def _getaddrinfo(host, port, *args, **kwargs):
    try:
        res = _original(host, port, *args, **kwargs)
    except socket.gaierror:
        if not isinstance(host, str) or host.replace(".", "").isdigit() or host in ("localhost",):
            raise
        ips = _doh(host) or (_cache.get(host, ([], 0))[0])
        if not ips:
            raise
        _cache[host] = (ips, time.time())
        _salvar()
        for ip in ips:
            try:
                return _original(ip, port, *args, **kwargs)
            except socket.gaierror:
                continue
        raise
    try:
        ips = list({r[4][0] for r in res if r[0] == socket.AF_INET})
        if ips and isinstance(host, str) and not host.replace('.', '').isdigit() and _cache.get(host, ([], 0))[0] != ips:
            _cache[host] = (ips, time.time())
            _salvar()
    except Exception:
        pass
    return res


def instalar():
    global _instalado
    if _instalado:
        return
    _instalado = True
    _carregar()
    socket.getaddrinfo = _getaddrinfo
