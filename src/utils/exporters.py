import os


def export_html(results, name):

    os.makedirs("data", exist_ok=True)

    file = f"data/report_{name.replace(' ','_')}.html"

    html = "<html><body>"
    html += f"<h1>Relatório OSINT - {name}</h1>"

    for r in results:
        html += f"<p><a href='{r}'>{r}</a></p>"

    html += "</body></html>"

    with open(file, "w", encoding="utf8") as f:
        f.write(html)

    print("Relatório salvo:", file)