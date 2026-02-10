[![Python](https://img.shields.io/badge/python-3.8+-blue.svg)](https://www.python.org/)
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](https://opensource.org/licenses/MIT)

# 🌐 Hosts file generator — quick and reliable

Lightweight script to generate a ready-to-use `hosts` file from a list of domains. It resolves IP addresses, deduplicates entries and can append results to the system `hosts` file.

If you prefer Russian documentation, see `README.md`.

## Quick start

1. (Recommended) create a virtual environment and install dependencies:

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```

2. Put domains into `general.txt` (one domain per line).

3. Run the generator:

```bash
python3 hosts_generator.py
```

By default a local `hosts` file will be created. The script will also attempt to save a backup of the system `hosts` to `hosts.backup` at startup.

## Features

- Parallel domain resolution with configurable workers
- Alternative DNS support via `dnspython` (Google / Cloudflare)
- Progress bar with `tqdm`
- Fallback: similar domain search + TLD variants to improve success rate
- Automatic backup of the system `hosts` to `hosts.backup` in the working dir
- Interactive selection of input `.txt` file

## Install

Install dependencies from `requirements.txt`:

```bash
pip install -r requirements.txt
```

Or install optional helpers only:

```bash
pip install dnspython tqdm
```

## Notes & safety

- Appending to the system `hosts` requires admin rights.
- The script attempts to read `/etc/hosts` (or Windows hosts) and save it as `hosts.backup`. If it fails you'll see a warning but the script continues.
- After changing the `hosts` file you may need to flush DNS cache.

## Examples

Generate hosts locally:

```bash
echo example.com > general.txt
python3 hosts_generator.py
cat hosts
```

Append generated entries to system `hosts` (Linux/macOS):

```bash
sudo cat hosts >> /etc/hosts
```

## Troubleshooting

If domains are not resolved consider installing `dnspython`, checking network/DNS or trying another DNS/VPN.

## Project layout

```
. 
├── hosts_generator.py    # main generator
├── general.txt           # sample domain list
├── hosts                 # generated output
├── hosts.backup          # created automatically at startup
└── README.en.md          # English documentation
```

## License

MIT — see `LICENSE`.

Contributions welcome: fork, branch, PR.
