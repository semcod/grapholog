from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import sys
from . import __version__
from .compare import compare, duplicates
from .render import render_compare, render_project, safe, to_dot
from .scan import scan


def nonnegative(value):
    number = int(value)
    if number < 0:
        raise argparse.ArgumentTypeError("must be >= 0")
    return number


def similarity(value):
    number = float(value)
    if not 0 <= number <= 1:
        raise argparse.ArgumentTypeError("must be between 0 and 1")
    return number


def parser():
    p = argparse.ArgumentParser(prog="grapholog", description="Jednorazowa mapa plikow, skladni i zaleznosci; bez wykonywania kodu projektu.")
    p.add_argument("--version", action="version", version=__version__)
    subs = p.add_subparsers(dest="command", required=True)
    common = argparse.ArgumentParser(add_help=False)
    common.add_argument("--all", dest="all_files", action="store_true", help="wylacz domyslne wykluczenia i .graphologignore")
    common.add_argument("--exclude", action="append", default=[], metavar="GLOB", help="wlasne wykluczenie; mozna powtarzac")
    common.add_argument("--source-root", action="append", default=[], metavar="DIR", help="korzen importow Python wzgledem projektu; domyslnie . oraz src")
    common.add_argument("--max-bytes", type=nonnegative, default=2 * 1024 * 1024, help="limit parsowania pliku; SHA-256 liczony zawsze; 0=bez limitu")
    common.add_argument("--min-nodes", type=nonnegative, default=12, help="minimalna liczba wezlow dla grup podobienstwa skladni/funkcji")
    common.add_argument("--cache-dir", type=Path, help="opcjonalny katalog cache parsera; pliki zrodlowe nadal sa odczytywane i haszowane")
    common.add_argument("--profile", choices=("inventory", "logic"), default="inventory", help="logic pomija dane i pliki generowane przed odczytem; zachowuje manifesty i licencje")
    common.add_argument("--ascii", action="store_true", help="widok zgodny z terminalem ASCII")
    common.add_argument("--color", choices=("auto", "always", "never"), default="auto")
    common.add_argument("--output", "-o", type=Path, help="zapis raportu do wskazanego pliku; bez nadpisania plikow zrodlowych")
    common.add_argument("--strict", action="store_true", help="kod 2 przy niepelnej analizie obslugiwanych jezykow/bledach odczytu")
    show = subs.add_parser("scan", parents=[common], help="pokaz mape jednego projektu")
    show.add_argument("path", nargs="?", default=".")
    show.add_argument("--format", choices=("terminal", "json", "dot"), default="terminal")
    show.add_argument("--detail", choices=("files", "compact", "full"), default="compact")
    diff = subs.add_parser("diff", parents=[common], help="porownaj dwa drzewa katalogow")
    diff.add_argument("left")
    diff.add_argument("right")
    diff.add_argument("--format", choices=("terminal", "json"), default="terminal")
    diff.add_argument("--threshold", type=similarity, default=0.70)
    diff.add_argument("--max-pairs", type=nonnegative, default=20000, help="maks. porownan przy szukaniu podobnych niedopasowanych plikow")
    diff.add_argument("--check", action="store_true", help="kod 1 jesli projekty roznia sie; przydatne w CI")
    return p


def incomplete(project) -> bool:
    return bool(project.warnings) or any(f.status in {"parse-error", "read-error", "changed-during-scan", "parser-missing"}
        or (f.language != "other" and f.status in {"size-limit", "non-utf8", "binary"}) for f in project.files)


def main(argv=None):
    args_list = list(sys.argv[1:] if argv is None else argv)
    if args_list and args_list[0] == "tickets":
        from .tickets_cli import main as tickets_main
        return tickets_main(args_list[1:])
    if args_list and args_list[0] in {"workspace", "contracts", "serve"}:
        from .workspace_cli import main as workspace_main
        return workspace_main(args_list)
    # `grapholog ./project` is shorthand for `grapholog scan ./project`.
    if not args_list or (args_list[0] not in {"scan", "diff", "-h", "--help", "--version"}):
        args_list.insert(0, "scan")
    args = parser().parse_args(args_list)
    try:
        options = dict(all_files=args.all_files, exclude=args.exclude, max_bytes=args.max_bytes,
                       source_roots=args.source_root or None, cache_dir=args.cache_dir, profile=args.profile)
        for root in args.source_root:
            if Path(root).is_absolute() or ".." in Path(root).parts:
                raise ValueError("--source-root must be a relative directory inside the project")
        output = args.output.expanduser().absolute() if args.output else None
        if output and output.exists():
            raise ValueError(f"Output exists: {output}. Choose a new report path.")
        if output and output.is_symlink():
            raise ValueError("Output cannot be a symbolic link")
        color = args.color == "always" or (args.color == "auto" and sys.stdout.isatty()
                                          and "NO_COLOR" not in os.environ and output is None)
        projects = []
        def run(path):
            opts = {**options, "exclude": list(options["exclude"])}
            if output:
                try:
                    opts["exclude"].append(output.resolve().relative_to(Path(path).expanduser().resolve()).as_posix())
                except ValueError:
                    pass
            project = scan(path, **opts)
            projects.append(project)
            return project
        if args.command == "scan":
            project = run(args.path)
            if args.format == "json":
                data = project.to_dict()
                data["duplicates"] = duplicates([("", project)], args.min_nodes)
                result = None
            elif args.format == "dot":
                result = to_dot(project)
            else:
                result = render_project(project, detail=args.detail, ascii_only=args.ascii,
                                        color=color, min_nodes=args.min_nodes)
            code = 0
        else:
            left, right = run(args.left), run(args.right)
            data = compare(left, right, threshold=args.threshold, max_pairs=args.max_pairs, min_nodes=args.min_nodes)
            result = None if args.format == "json" else render_compare(data, ascii_only=args.ascii, color=color)
            code = 1 if args.check and data["has_differences"] else 0
        def write_result(handle):
            if result is None:
                json.dump(data, handle, ensure_ascii=True, indent=2)
                handle.write("\n")
            else:
                handle.write(result)
        if output:
            with output.open("x", encoding="utf-8") as handle:
                write_result(handle)
            print(f"Saved: {safe(output)}", file=sys.stderr)
        else:
            try:
                write_result(sys.stdout)
            except UnicodeEncodeError:
                sys.stdout.write(result.encode(sys.stdout.encoding or "ascii", "backslashreplace").decode(sys.stdout.encoding or "ascii"))
        if args.strict and any(incomplete(project) for project in projects):
            return 2
        return code
    except BrokenPipeError:
        # Avoid a second flush failure when piping a long report to head.
        sys.stdout = open(os.devnull, "w")
        return 0
    except (OSError, ValueError) as exc:
        print("grapholog: " + safe(exc), file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
