"""Configure the private IP on which the Ubuntu user service listens."""
import argparse
import ipaddress
import os
from pathlib import Path


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('ip', help='Private IPv4 address assigned to this server')
    args = parser.parse_args()
    address = ipaddress.IPv4Address(args.ip)
    if not address.is_private or address.is_loopback:
        parser.error('The server address must be a private LAN IPv4 address')
    target = Path.home() / '.config' / 'channel-keeper' / 'lan.env'
    target.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    os.chmod(target.parent, 0o700)
    fd = os.open(target, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    os.fchmod(fd, 0o600)
    with os.fdopen(fd, 'w', encoding='utf-8') as stream:
        stream.write(f'CHANNEL_KEEPER_LAN_IP={address}\n')
    print(f'LAN address: {address}')
    print('Default browser login: keeper / keeper. Change it in Preferences after first login.')


if __name__ == '__main__':
    main()
