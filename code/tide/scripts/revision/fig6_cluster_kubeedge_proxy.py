#!/usr/bin/env python3
"""Experiment-owned transparent TCP forwarding, only for KE transport/HTTP."""
import asyncio
import json
from pathlib import Path
import resource
import sys

async def relay(reader, writer):
    try:
        while data := await reader.read(65536):
            writer.write(data)
            await writer.drain()
    finally:
        writer.close()

async def connection(reader, writer, target, port):
    try:
        remote_reader, remote_writer = await asyncio.open_connection(target, port)
        await asyncio.gather(relay(reader, remote_writer), relay(remote_reader, writer))
    except (ConnectionError, OSError):
        writer.close()

async def main():
    target_file = Path(__file__).with_name('cloud-target-ip')
    cloud_ip = target_file.read_text().strip() if target_file.exists() else '10.4.1.4'
    mappings = [('11.0.4.47', 29880, cloud_ip, 10000),
                ('11.0.4.47', 29882, cloud_ip, 10002),
                ('11.0.4.47', 29890, cloud_ip, 29890),
                ('11.0.4.47', 29891, cloud_ip, 29891)] if sys.argv[1] == 'cloud' else [
                ('10.4.1.2', 10000, '11.0.4.47', 29880),
                ('10.4.1.2', 10002, '11.0.4.47', 29882)]
    servers = []
    for addr, port, target, target_port in mappings:
        servers.append(await asyncio.start_server(
            lambda r, w, t=target, p=target_port: connection(r, w, t, p), addr, port))
    await asyncio.gather(*(server.serve_forever() for server in servers))

if __name__ == '__main__':
    # Two descriptors per forwarded connection; the largest trace offers 3040.
    soft, hard = resource.getrlimit(resource.RLIMIT_NOFILE)
    if hard != resource.RLIM_INFINITY and hard < 65536:
        raise RuntimeError('RLIMIT_NOFILE hard limit below required 65536')
    resource.setrlimit(resource.RLIMIT_NOFILE,
                       (soft if soft == resource.RLIM_INFINITY else max(65536, soft), hard))
    print(json.dumps({'rlimit_nofile': resource.getrlimit(resource.RLIMIT_NOFILE)}), flush=True)
    asyncio.run(main())
