# -*- coding: utf-8 -*-
"""
Download all URLs in a GES DISC txt list, handling Earthdata (URS) redirects correctly.
Pure downloader: no data parsing, just download.

Works well when you get 401 due to cross-domain redirect auth stripping.
"""

import os
import time
from pathlib import Path
from urllib.parse import urlparse, parse_qs, unquote

import requests


# ====== EDIT ME ======
EARTHDATA_USERNAME = "correr27890"
EARTHDATA_PASSWORD = "AQN/RZ2Y&S5Rb+j"


TXT_PATH = r"C:\DOCUMENTO\Sand-and-Dust-Storms-and-Human-Health\data_prep\merra-2\subset_M2T1NXAER_5.12.4_20260216_074723_.txt"
OUT_DIR = r"./downloads_merra2_subset"

DOWNLOAD_PDF = False
RETRIES = 5
SLEEP_BETWEEN = 0.2
# =====================


def is_netcdf_signature(head: bytes) -> bool:
    # NetCDF classic: b"CDF", NetCDF4(HDF5): b"\x89HDF\r\n\x1a\n"
    return head.startswith(b"CDF") or head.startswith(b"\x89HDF\r\n\x1a\n")


def is_html_signature(head: bytes) -> bool:
    h = head.lstrip().lower()
    return h.startswith(b"<!doctype html") or h.startswith(b"<html")


def is_valid_local_netcdf(path: Path) -> bool:
    try:
        with open(path, "rb") as f:
            head = f.read(16)
        return is_netcdf_signature(head)
    except Exception:
        return False


def is_url(s: str) -> bool:
    return s.startswith("http://") or s.startswith("https://")


def filename_from_url(url: str) -> str:
    parsed = urlparse(url)
    qs = parse_qs(parsed.query)

    if "LABEL" in qs and qs["LABEL"]:
        return qs["LABEL"][0]

    if "FILENAME" in qs and qs["FILENAME"]:
        fn_path = unquote(qs["FILENAME"][0])
        return os.path.basename(fn_path)

    base = os.path.basename(parsed.path)
    return base if base else "download.bin"


def read_urls(txt_path: Path):
    urls = []
    with open(txt_path, "r", encoding="utf-8", errors="ignore") as f:
        for line in f:
            u = line.strip()
            if not u or not is_url(u):
                continue
            if (not DOWNLOAD_PDF) and u.lower().endswith(".pdf"):
                continue
            urls.append(u)
    # 去重但保序
    seen = set()
    out = []
    for u in urls:
        if u not in seen:
            out.append(u)
            seen.add(u)
    return out


def get_follow_redirects_with_urs_auth(session: requests.Session, url: str, auth, max_hops: int = 10):
    """
    Manually follow redirects.
    Key point: when redirect target is urs.earthdata.nasa.gov, attach BasicAuth.
    """
    cur = url
    for _ in range(max_hops):
        r = session.get(cur, stream=True, allow_redirects=False, timeout=(30, 600))
        # final
        if r.status_code < 300 or r.status_code >= 400:
            return r

        loc = r.headers.get("Location")
        if not loc:
            return r

        # Some redirects provide relative URLs
        if loc.startswith("/"):
            p = urlparse(cur)
            loc = f"{p.scheme}://{p.netloc}{loc}"

        # When redirecting to URS, add auth
        if "urs.earthdata.nasa.gov" in loc:
            r.close()
            r = session.get(loc, stream=True, allow_redirects=False, timeout=(30, 600), auth=auth)
            # After URS, continue following whatever it redirects to
            cur = r.headers.get("Location", loc)
            r.close()
        else:
            r.close()
            cur = loc

    # too many hops
    raise RuntimeError("Too many redirects (possible auth/EULA issue).")


def download_one(session: requests.Session, url: str, out_path: Path, auth, retries: int = 5) -> bool:
    out_path.parent.mkdir(parents=True, exist_ok=True)
    tmp_path = out_path.with_suffix(out_path.suffix + ".part")

    for attempt in range(1, retries + 1):
        try:
            r = get_follow_redirects_with_urs_auth(session, url, auth)

            if r.status_code in (401, 403):
                # very often: EULA not accepted OR wrong credentials
                raise RuntimeError(
                    f"AUTH {r.status_code}. 常见原因：\n"
                    f"1) Earthdata 用户名/密码不对\n"
                    f"2) EULAs 未同意（去 Profile -> EULAs）\n"
                    f"3) GES DISC 授权刚改完未生效（等几分钟再试）"
                )

            r.raise_for_status()

            total = int(r.headers.get("Content-Length", "0"))
            content_type = (r.headers.get("Content-Type") or "").lower()
            got = 0
            first_bytes = b""
            with open(tmp_path, "wb") as f:
                for chunk in r.iter_content(chunk_size=1024 * 1024):
                    if chunk:
                        if not first_bytes:
                            first_bytes = chunk[:64]
                        f.write(chunk)
                        got += len(chunk)

            r.close()

            if total > 0 and got < total:
                raise IOError(f"Incomplete download: {got}/{total}")

            if not first_bytes:
                raise IOError("Empty response body.")

            if is_html_signature(first_bytes) or ("text/html" in content_type):
                preview = first_bytes[:40].decode("utf-8", errors="ignore")
                raise RuntimeError(
                    "Downloaded HTML page instead of NetCDF "
                    f"(content-type={content_type}, head={preview!r}). "
                    "Likely Earthdata auth/EULA issue."
                )

            if not is_netcdf_signature(first_bytes):
                preview = " ".join(f"{b:02X}" for b in first_bytes[:8])
                raise RuntimeError(
                    f"Downloaded file is not NetCDF (head hex: {preview})."
                )

            tmp_path.replace(out_path)
            return True

        except Exception as e:
            print(f"[RETRY {attempt}/{retries}] {out_path.name} -> {e}")
            time.sleep(2 * attempt)

    try:
        if tmp_path.exists():
            tmp_path.unlink()
    except Exception:
        pass
    return False


def main():
    if "PUT_YOURS_HERE" in (EARTHDATA_USERNAME, EARTHDATA_PASSWORD):
        raise RuntimeError("先把 EARTHDATA_USERNAME / EARTHDATA_PASSWORD 换成你自己的。")

    txt_path = Path(TXT_PATH)
    if not txt_path.exists():
        raise FileNotFoundError(f"找不到 TXT_PATH: {txt_path.resolve()}")

    out_dir = Path(OUT_DIR)
    out_dir.mkdir(parents=True, exist_ok=True)

    urls = read_urls(txt_path)
    print(f"Found {len(urls)} URLs.")

    session = requests.Session()
    session.headers.update({"User-Agent": "gesdisc-downloader-fixed401/1.0"})

    auth = (EARTHDATA_USERNAME, EARTHDATA_PASSWORD)

    ok = 0
    for i, url in enumerate(urls, 1):
        fname = filename_from_url(url)
        out_path = out_dir / fname

        if out_path.exists() and out_path.stat().st_size > 0:
            if is_valid_local_netcdf(out_path):
                print(f"[SKIP] ({i}/{len(urls)}) {fname}")
                ok += 1
                continue
            print(f"[REGET] ({i}/{len(urls)}) {fname} exists but is not valid NetCDF")

        print(f"[GET ] ({i}/{len(urls)}) {fname}")
        if download_one(session, url, out_path, auth, retries=RETRIES):
            print(f"[OK  ] {fname}")
            ok += 1
        else:
            print(f"[FAIL] {fname}")

        time.sleep(SLEEP_BETWEEN)

    print(f"\nDONE. success={ok}/{len(urls)}")
    print(f"Output: {out_dir.resolve()}")


if __name__ == "__main__":
    main()
