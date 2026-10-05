# bin/

`sub_install.py` looks for a `cloudflared` **linux/arm64** binary here:

```
bin/cloudflared-linux-arm64
```

The binary is ~37 MB and is **not committed** to this repo. It is resolved in this order:

1. `--binary <path>` on the command line
2. `bin/cloudflared-linux-arm64` (this folder)
3. `./cloudflared-linux-arm64` in the current working directory
4. `./UG65/_serve/` or `./UG65/_pki/` (legacy cache locations)
5. Download from Cloudflare's GitHub release into this folder (about 37 MB)

To fetch it manually:

```bash
curl -L -o bin/cloudflared-linux-arm64 \
  https://github.com/cloudflare/cloudflared/releases/latest/download/cloudflared-linux-arm64
```

Or copy it off a gateway that already has it (this is how it was originally obtained —
saves the gateway's mobile data):

```bash
scp root@<gateway-ip>:/usr/bin/cloudflared ./bin/cloudflared-linux-arm64
```

Verify the architecture matches before pushing it to a gateway:

```bash
file bin/cloudflared-linux-arm64      # expect: ELF 64-bit LSB executable, ARM aarch64
```

> Note: on these gateways `uname -m` is deliberately rewritten by Milesight to return
> `x.x.x`. Read `DISTRIB_ARCH` from `/etc/openwrt_release` instead — `aarch64` means you
> need the `arm64` build.
