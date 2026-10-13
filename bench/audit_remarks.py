"""Extract and exercise only the std-only lexer from the supplied proposal.

Usage: python bench/audit_remarks.py remarks.txt --rustc /path/to/rustc --output /tmp/audit
The extracted code contains no file/network operations. The complete proposed
application is not executed. Its unmodified Cargo manifest can be checked separately.
"""
import argparse
import json
from pathlib import Path
import subprocess

CASES = {
    'number': ('def f(x):\n    return x < 10\n','def f(x):\n    return x < 1000\n'),
    'string': ('def f():\n    return "allow"\n','def f():\n    return "deny"\n'),
    'indent': ('def f(x):\n    if x:\n        x += 1\n        return x\n',
               'def f(x):\n    if x:\n        x += 1\n    return x\n'),
    'fstring': ('def f(x):\n    return f"{x + 1}"\n','def f(x):\n    return f"{x - 1}"\n'),
}


def main():
    parser=argparse.ArgumentParser()
    parser.add_argument('remarks',type=Path)
    parser.add_argument('--rustc',default='rustc')
    parser.add_argument('--output',type=Path,required=True)
    args=parser.parse_args()
    code=args.remarks.read_text().split('```rust\n',1)[1].split('\n```',1)[0]
    keyword=code[code.index('const KW:'):code.index('const IGNORE_DIRS:')]
    lexer=code[code.index('#[derive(Clone, Copy, PartialEq)]'):code.index('fn lex_text(')]
    body='use std::collections::HashMap;\n'+keyword+lexer+'\nfn main() {\n'
    for label,(a,b) in CASES.items():
        body+=f' println!("{label}: canonical_equal={{}}", canon_tokens(&lex({json.dumps(a)})) == canon_tokens(&lex({json.dumps(b)})));\n'
    body+='}\n'
    args.output.mkdir(parents=True,exist_ok=True)
    source=args.output/'lexer.rs';source.write_text(body)
    binary=args.output/'lexer'
    compiled=subprocess.run([args.rustc,'--edition=2021',str(source),'-o',str(binary)],capture_output=True,text=True)
    (args.output/'compile.txt').write_text(compiled.stdout+compiled.stderr)
    compiled.check_returncode()
    output=subprocess.run([str(binary)],capture_output=True,text=True,check=True).stdout
    (args.output/'results.txt').write_text(output)
    print(output,end='')


if __name__=='__main__':main()
