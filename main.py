#!/usr/bin/env python3
from __future__ import annotations

import argparse
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT))

from invoice_agent.config import get_settings
from invoice_agent.observability.console import progress
from invoice_agent.runner import process_demo, process_path, resume_review
from invoice_agent.storage import clear_processed_ledger, ensure_database


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Multi-agent invoice processing")
    parser.add_argument("--invoice_path", help="Invoice file or directory")
    parser.add_argument("--output", default="outputs")
    parser.add_argument("--provider", choices=["mock", "xai", "ollama", "openai", "anthropic"])
    parser.add_argument("--require-llm", action="store_true")
    parser.add_argument(
        "--fresh",
        action="store_true",
        help="Clear processed-invoice ledger before run (avoids DEDUP on re-demos)",
    )
    parser.add_argument("--demo", choices=["vp-review"])
    parser.add_argument("--resume")
    parser.add_argument("--vp-decision", choices=["approve", "reject"])
    parser.add_argument("--vp-actor", default="VP")
    parser.add_argument("--vp-reason", default="")
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    settings = get_settings(
        provider_override=args.provider,
        require_llm=args.require_llm,
    )
    if args.require_llm and settings.provider == "mock":
        print("error: --require-llm needs XAI_API_KEY or --provider=xai|ollama|openai|anthropic", file=sys.stderr)
        return 2
    output_dir = Path(args.output)
    if not output_dir.is_absolute():
        output_dir = ROOT / output_dir
    ensure_database(settings)
    if args.fresh:
        cleared = clear_processed_ledger(settings)
        progress(
            "ledger cleared",
            detail=f"processed={cleared['processed_invoices']} payments={cleared['payment_attempts']}",
        )
    if args.resume:
        if not args.vp_decision:
            print("error: --resume requires --vp-decision", file=sys.stderr)
            return 2
        resume_review(settings, args.resume, args.vp_decision, args.vp_actor, args.vp_reason, output_dir)
        return 0
    if args.demo == "vp-review":
        process_demo(settings, output_dir)
        return 0
    if not args.invoice_path:
        print("error: provide --invoice_path, --demo=vp-review, or --resume", file=sys.stderr)
        return 2
    process_path(settings, args.invoice_path, output_dir)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
