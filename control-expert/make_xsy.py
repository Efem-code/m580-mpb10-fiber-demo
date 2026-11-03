"""Turn variables.csv into variables.xsy, a Control Expert variable import file.

  python make_xsy.py        then in Control Expert: Data Editor > right-click > Import (or File > Import),
                            pick variables.xsy
"""
import csv
import os
import time
from xml.sax.saxutils import quoteattr

HERE = os.path.dirname(os.path.abspath(__file__))


def main():
    with open(os.path.join(HERE, "variables.csv"), encoding="utf-8", newline="") as f:
        rows = list(csv.DictReader(f, delimiter=";"))
    now = time.strftime("%Y-%m-%d-%H:%M:%S")
    out = ['<?xml version="1.0" encoding="UTF-8" standalone="yes"?>',
           '<VariablesExchangeFile>',
           f'<fileHeader company="Schneider Automation" product="EcoStruxure Control Expert" dateTime="date_and_time#{now}" '
           'content="Variable source file" DTDVersion="41"></fileHeader>',
           f'<contentHeader name="M580 MPB10 demo" version="0.0.000" dateTime="date_and_time#{now}"></contentHeader>',
           '<dataBlock>']
    for r in rows:
        attrs = f'name={quoteattr(r["Name"])} typeName={quoteattr(r["Type"])}'
        if r["Address"]:
            attrs += f' topologicalAddress={quoteattr(r["Address"])}'
        out.append(f'<variables {attrs}>')
        if r["Comment"]:
            out.append(f'<comment>{r["Comment"].replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")}</comment>')
        if r["Initial value"]:
            out.append(f'<variableInit value={quoteattr(r["Initial value"])}></variableInit>')
        out.append('</variables>')
    out += ['</dataBlock>', '</VariablesExchangeFile>', '']
    with open(os.path.join(HERE, "variables.xsy"), "w", encoding="utf-8", newline="\r\n") as f:
        f.write("\n".join(out))
    print(f"variables.xsy: {len(rows)} variables")


if __name__ == "__main__":
    main()
