"""Generate a self-contained report from the measured integration artifacts."""
import html
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / 'results/tickets'


def main():
    data = json.loads((OUT / 'planning-benchmark.json').read_text())
    demo = json.loads((OUT / 'integration-demo.json').read_text())
    plan = json.loads((OUT / 'example-plan.json').read_text())
    current = json.loads((OUT / 'example-status.json').read_text())
    ids = {r['task_key']: r['project'] + ':' + r['ticket_id'] for r in current['tickets']}
    stages = {'design': 'Projekt API', 'package': 'Paczka / kod', 'verify': 'Artefakt i testy',
              'migrate': 'Migracja', 'validate': 'Walidacja workspace', 'route-review': 'Wybór paczki'}
    rows = ''.join('<tr><td>' + html.escape(ids[t['key']]) + '</td><td>' + stages[t['stage']] + '</td><td>' +
                   html.escape(', '.join(ids[d] for d in t['depends_on']) or '—') + '</td><td>' +
                   html.escape(t['proposal']['name']) + '</td></tr>' for t in plan['tasks'])
    target_rows = ''.join('<tr><td>' + html.escape(t['name']) + '</td><td>' +
        ('Nowa paczka' if t['action'] == 'create' else 'Uzupełnienie / wykorzystanie istniejącej') + '</td><td>' +
        html.escape(t['project'] + '/' + t['path']) + '</td></tr>' for t in plan['targets'])
    report = f'''<!doctype html><html lang="pl"><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>Grapholog 0.5 — Planfile i Koru</title><style>
:root{{color-scheme:light dark;--bg:#f6f7fb;--card:#fff;--ink:#172338;--muted:#526279;--line:#d6deea;--accent:#2255c9}}
@media(prefers-color-scheme:dark){{:root{{--bg:#0c1423;--card:#152137;--ink:#edf3ff;--muted:#b0bed3;--line:#344761;--accent:#8eb7ff}}}}
*{{box-sizing:border-box}}body{{margin:0;background:var(--bg);color:var(--ink);font:16px/1.65 system-ui,sans-serif}}main{{max-width:1100px;margin:auto;padding:36px 24px 70px}}
h1{{font-size:clamp(30px,5vw,52px);line-height:1.15;margin:.3em 0}}h2{{margin-top:2em;line-height:1.3}}p{{max-width:900px}}a{{color:var(--accent)}}code,pre{{font-family:ui-monospace,monospace}}code{{overflow-wrap:anywhere}}
pre{{background:var(--card);padding:20px;border:1px solid var(--line);overflow:auto;border-radius:12px}}.muted{{color:var(--muted)}}.stats{{display:grid;grid-template-columns:repeat(auto-fit,minmax(150px,1fr));gap:12px;margin:28px 0}}.stat{{background:var(--card);border:1px solid var(--line);border-radius:12px;padding:18px}}.stat b{{display:block;font-size:30px;color:var(--accent)}}.table{{overflow:auto}}table{{border-collapse:collapse;width:100%;background:var(--card)}}td,th{{border-bottom:1px solid var(--line);text-align:left;padding:12px;vertical-align:top}}th{{font-size:13px;text-transform:uppercase;color:var(--muted)}}.note{{border-left:4px solid var(--accent);padding:16px 22px;background:var(--card)}}svg{{width:100%;height:auto}}svg text{{fill:var(--ink);font:15px system-ui,sans-serif}}svg rect{{fill:var(--card);stroke:var(--line)}}svg path{{stroke:var(--accent);fill:none;stroke-width:2}}
</style><main><p class="muted">GRAPHOLOG / WYDANIE 0.5.0 / 1 PAŹDZIERNIKA 2026</p>
<h1>Od duplikatu do wspólnej paczki</h1><p>Planowanie, powiązane tickety Planfile, kontrolowane przekazanie do Koru i dostarczenie zweryfikowanych artefaktów konsumentom. Kod analizatora i integracji jest w paczce Python + Rust.</p>
<div class="stats"><div class="stat"><b>{data['projects']}</b>projektów w pomiarze</div><div class="stat"><b>{data['targets']}</b>kandydatów na paczki</div><div class="stat"><b>{data['tasks']}</b>powiązanych ticketów</div><div class="stat"><b>{data['median_seconds']:.2f} s</b>mediana planowania</div></div>
<svg viewBox="0 0 1000 310" role="img" aria-label="Projekt API, budowa paczki i weryfikacja artefaktu poprzedzają migracje trzech projektów; po nich następuje walidacja workspace.">
<path d="M240 55 H350 M590 55 H700 M820 85 V135 H170 V170 M820 135 H500 V170 M820 135 V170 M170 220 V265 H390 M500 220 V265 M820 220 V265 H610"/>
<rect x="30" y="25" width="210" height="60" rx="12"/><text x="135" y="62" text-anchor="middle">Projekt API</text>
<rect x="350" y="25" width="240" height="60" rx="12"/><text x="470" y="62" text-anchor="middle">Utwórz / uzupełnij paczkę</text>
<rect x="700" y="25" width="260" height="60" rx="12"/><text x="830" y="62" text-anchor="middle">Artefakt + testy + SHA-256</text>
<rect x="50" y="170" width="240" height="50" rx="10"/><text x="170" y="201" text-anchor="middle">Migracja billing</text>
<rect x="380" y="170" width="240" height="50" rx="10"/><text x="500" y="201" text-anchor="middle">Migracja orders</text>
<rect x="700" y="170" width="240" height="50" rx="10"/><text x="820" y="201" text-anchor="middle">Migracja analytics</text>
<rect x="390" y="242" width="220" height="50" rx="10"/><text x="500" y="273" text-anchor="middle">Walidacja workspace</text></svg>
<p class="muted">Schemat jednego zestawu zależności. Rzeczywisty przykład poniżej ma dwóch producentów i wspólne tickety migracji.</p>
<h2>Jak wybierany jest cel</h2><p>Jawna reguła routingu ma pierwszeństwo. Bez niej wspólna funkcja znaleziona wewnątrz zarejestrowanej paczki wskazuje istniejący cel. Brak celu daje propozycję nowej paczki; kilku kandydatów daje ticket wyboru. Normalizacja AST nie dowodzi równoważności zachowania. Dlatego każdy wariant zaczyna się od projektu API i oceny zależności, efektów oraz licencji.</p>
<div class="table"><table><thead><tr><th>Przykładowy cel</th><th>Decyzja</th><th>Lokalizacja</th></tr></thead><tbody>{target_rows}</tbody></table></div>
<h2>Rzeczywiście utworzone lokalne tickety</h2><p>W przykładzie czterech projektów oficjalne API Planfile utworzyło <b>{demo['created_first_run']}</b> ticketów. Powtórzenie utworzyło <b>{demo['created_second_run']}</b>, zachowując wszystkie identyfikatory. Początkowo gotowe są dwa zadania projektu API; kolejne etapy czekają na dowody.</p>
<div class="table"><table><thead><tr><th>Projekt : ID Planfile</th><th>Etap</th><th>Zależy od</th><th>Cel</th></tr></thead><tbody>{rows}</tbody></table></div>
<h2>Planfile i GitHub</h2><p>Propozycje przechodzą przez <code>TicketProposalV1</code>, a zapis przez <code>create_ticket_deduplicated</code>. Grapholog nie edytuje YAML ticketów ręcznie. Planfile pozostaje źródłem statusu, mapowań GitHub i potwierdzeń synchronizacji. Grapholog przechowuje plan, wiązania ticketów i dziennik prób delegacji.</p>
<p>Synchronizacja obejmuje wyłącznie tickety danego planu; odczyt korzysta z konkretnych przypisanych issues. Linki między repozytoriami trafiają do opisów. Częściowe powodzenie jest zachowywane, a ponowienie nie tworzy ponownie już przypisanych issues. Repozytoria wskazuje jawny katalog, sprawdzany względem konfiguracji Planfile i Git origin.</p>
<h2>Koru i przekazanie artefaktów</h2><pre>koru ticket auto ID --project ROOT --queue grapholog --no-sync</pre>
<p>Sprawdzono rzeczywiste polecenie Koru w trybie <code>--dry-run</code>: kod wyjścia 0, rozpoznany ticket, wykonawca <code>llm</code> i plik kontekstu. Uruchomienie produkcyjne wymaga własnej konfiguracji wykonawcy/modelu Koru. Pliki kontekstu zawierają zamknięty JSON Schema wyniku oraz GBNF; Grapholog nie ustawia sam constrained decoding w dostawcy Koru.</p>
<p>Każdy etap dostarcza <code>grapholog.task-result.v1</code> związany z zadaniem i digestem wejścia. Weryfikacja paczki wymaga wersji, eksportów, testów z logami i dowodu przeglądu licencji. Manifest wyniku musi być dołączony do ticketu. Zweryfikowane pliki są kopiowane do lokalnego katalogu konsumenta przed przekazaniem zadania Koru.</p>
<div class="note">Status done, zamknięcie issue lub exit code 0 nie wystarcza do migracji. Grapholog sprawdza sumy i strukturę dowodów oraz deklarowane wyniki; nie dowodzi, że raport modelu jest prawdziwy i nie uruchamia sam wszystkich testów. Anulowane zależności, needs_review, nieaktualny kod i brak artefaktu blokują dalszą pracę.</div>
<h2>Co zostało przetestowane</h2><ul><li>75 testów Pythona, w tym 20 scenariuszy ekstrakcji; 2 testy Rust.</li>
<li>15 przypadków gramatyk w natywnym walidatorze llama.cpp, bez modelu.</li><li>Nowy i istniejący pakiet, niejednoznaczny katalog, scalanie grup oraz jeden ticket migracji na projekt.</li>
<li>Powtórzenia, odzyskanie mapowań, terminalne tickety, stare źródła, symlinki i zmienione artefakty.</li>
<li>Prawdziwy store i synchronizator Planfile; symulator granicy GitHub, w tym częściowa awaria oraz zamknięcie issue bez dowodów.</li>
<li>Delegacja do procesów testowych, kod 0 bez ukończenia oraz timeout. Prawdziwy Koru: parser i dry-run.</li>
<li>Zgodny plan przez CLI, MCP, REST i gRPC.</li></ul>
<p><b>Nie wykonano wywołania LLM, automatycznego przeniesienia kodu użytkownika ani publikacji issues do jego konta GitHub.</b> W paczce są implementacja, testy i odtwarzalny lokalny przykład; efekty zdalne i kod wykonuje skonfigurowany Koru po uruchomieniu komend wykonawczych.</p>
<h2>Metodologia pomiaru</h2><p>Planner 0.5 pracował na zapisanym syntetycznym indeksie 0.4: 200 projektów JS/TS, 4600 wybranych plików. Trzy przebiegi: {', '.join(f'{t:.3f}' for t in data['seconds'])} s. Pomiar obejmuje odczyt SQLite, wybór celów, DAG i walidację JSON Schema; nie obejmuje parsowania źródeł, zapisu Planfile, GitHub ani pracy LLM. Wybrano 20 rodzin źródłowych; funkcje testowe pozostały poza kandydatami. Plan ma {data['plan_json_bytes']:,} bajtów w zmierzonym kodowaniu JSON. To pomiar tego korpusu, nie limit ani gwarancja dla dowolnego workspace.</p>
<h2>Użycie</h2><pre>grapholog tickets plan --db index.sqlite --config catalog.json -o plan.json
grapholog tickets materialize plan.json --db index.sqlite --state state.json
grapholog tickets sync --state state.json --execute
grapholog tickets run --state state.json --max-steps 20 --execute --sync-github
grapholog tickets catalog --state state.json -o next-catalog.json</pre>
<p>Katalog zweryfikowanych paczek może zasilić następną analizę. Nowe paczki powstają we wskazanym projekcie; adapter nie tworzy zdalnych repozytoriów ani nie publikuje automatycznie do PyPI/npm. Katalog, routing i skonfigurowany wykonawca są częścią wdrożenia.</p>
<h2>Źródła i wersje integracji</h2><ul>
<li><a href="https://github.com/semcod/planfile/blob/dc427042c831b4bf0af3d18f82de071004a490c1/docs/PUBLIC_CONTRACTS.md">Planfile: publiczne kontrakty</a> — commit dc427042, wersja 0.1.126.</li>
<li><a href="https://github.com/semcod/planfile/blob/dc427042c831b4bf0af3d18f82de071004a490c1/planfile/sync/outbound.py">Planfile: synchronizacja i potwierdzenia</a>.</li>
<li><a href="https://github.com/semcod/koru/blob/cca08dc1897fe34caa17fd7a65a4a8077a195727/src/koru/cli_ticket_queue.py">Koru: kontrakt CLI ticket auto</a> — commit cca08dc1, wersja 0.1.461.</li>
</ul><p class="muted">Kod i pełne wyniki: README.md, contracts/, examples/extraction/, results/tickets/ oraz bench/ticket_demo.py i bench/planning_benchmark.py w archiwum wydania.</p></main></html>'''
    (ROOT / 'RAPORT.html').write_text(report, encoding='utf-8')
    print('RAPORT.html:', len(report.encode()), 'bytes')


if __name__ == '__main__':main()
