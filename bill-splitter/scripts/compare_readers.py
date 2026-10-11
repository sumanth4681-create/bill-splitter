"""Run both receipt readers on the same photos and print a comparison table.

Usage (from bill-splitter/):  python scripts/compare_readers.py photo1.jpg photo2.png ... [--rows]

Google reads run in parallel with each other and with the local reads (network-bound vs
GPU-bound). Local reads run one at a time: LM Studio serves one request at a time, so more
threads there would only queue. Saved responses in backend/fixtures/ are replayed, so a
second run costs nothing.
"""

import sys
import time
from collections import Counter
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from dotenv import load_dotenv

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
load_dotenv(Path(__file__).resolve().parent.parent / ".env")

from backend import ocr  # noqa: E402
from backend.models import ParsedReceipt  # noqa: E402

MIME = {".jpg": "image/jpeg", ".jpeg": "image/jpeg", ".png": "image/png", ".webp": "image/webp"}


def read(path: Path, reader: str) -> tuple[float, ParsedReceipt | str]:
    started = time.perf_counter()
    try:
        receipt = ocr.read_receipt(path.read_bytes(), MIME.get(path.suffix.lower(), "image/jpeg"), reader=reader)
    except Exception as exc:  # report the failure in the table instead of stopping the run
        return time.perf_counter() - started, f"{type(exc).__name__}: {exc}"
    return time.perf_counter() - started, receipt


def money(cents: int) -> str:
    return f"{cents / 100:,.2f}"


def main(argv: list[str]) -> None:
    show_rows = "--rows" in argv
    paths = [Path(a) for a in argv if not a.startswith("--")]
    if not paths:
        print(__doc__)
        return

    with ThreadPoolExecutor(max_workers=len(paths) + 1) as pool:
        google = {p: pool.submit(read, p, "docai") for p in paths}
        local_runner = pool.submit(lambda: {p: read(p, "local") for p in paths})
        google_results = {p: f.result() for p, f in google.items()}
        local_results = local_runner.result()

    header = f"{'receipt':22} {'reader':7} {'secs':>6} {'items':>5} {'common':>6} {'items $':>9} {'tax':>7} {'tip':>7} {'total':>9}  sums?"
    print(header)
    print("-" * len(header))
    for p in paths:
        results = {"docai": google_results[p], "local": local_results[p]}
        prices = {k: Counter(i.price_cents for i in v[1].items) for k, v in results.items() if isinstance(v[1], ParsedReceipt)}
        common = sum((prices["docai"] & prices["local"]).values()) if len(prices) == 2 else 0
        for reader, (secs, result) in results.items():
            if isinstance(result, str):
                print(f"{p.name[:22]:22} {reader:7} {secs:6.1f}  FAILED {result[:70]}")
                continue
            items_total = sum(i.price_cents for i in result.items)
            sums = items_total + result.tax_cents + result.tip_cents == result.total_cents
            print(f"{p.name[:22]:22} {reader:7} {secs:6.1f} {len(result.items):5} {common:6} {money(items_total):>9} "
                  f"{money(result.tax_cents):>7} {money(result.tip_cents):>7} {money(result.total_cents):>9}  {'yes' if sums else 'NO'}")
            if show_rows:
                for item in result.items:
                    print(f"{'':31}  {item.name[:40]:40} x{item.quantity:<3} {money(item.price_cents):>9}")
        print()


if __name__ == "__main__":
    main(sys.argv[1:])
