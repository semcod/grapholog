"""Generate the self-contained 0.3 release report from measured results."""
from pathlib import Path
import html
import json

ROOT = Path(__file__).resolve().parents[1]
DATA = ROOT / 'results/cache'
bench = json.loads((DATA / 'benchmark.json').read_text())
cases = {case['name']: case for case in bench['benchmarks']}
labels = {
    'python-0.2': 'Python 0.2', 'python-0.3-off': 'Python 0.3 · cache wyłączony',
    'python-0.3-cold': 'Python 0.3 · pusty cache', 'python-0.3-warm': 'Python 0.3 · rozgrzany cache',
    'rust-0.2': 'Rust 0.2 · 1 worker', 'rust-0.3-off': 'Rust 0.3 · 1 worker · cache wyłączony',
    'rust-0.3-cold': 'Rust 0.3 · 1 worker · pusty cache',
    'rust-0.3-warm': 'Rust 0.3 · 1 worker · rozgrzany cache',
    'rust-0.3-jobs4-off': 'Rust 0.3 · 4 workery · cache wyłączony',
    'rust-0.3-jobs4-warm': 'Rust 0.3 · 4 workery · rozgrzany cache',
}


def num(value, places=3):
    return f'{value:.{places}f}'.replace('.', ',')


def gain(case, old, new):
    s = cases[case]['summary']
    return num(s[old]['median_seconds'] / s[new]['median_seconds'], 2)


def table(case):
    rows = []
    for key, s in case['summary'].items():
        rows.append(f'<tr><td>{labels[key]}</td><td>{num(s["median_seconds"])}</td>'
                    f'<td>{num(s["min_seconds"])}–{num(s["max_seconds"])}</td>'
                    f'<td>{num(s["median_peak_rss_mib"], 1)}</td></tr>')
    return '<div class="table"><table><thead><tr><th>Wariant</th><th>Mediana [s]</th>' \
           '<th>Min.–maks. [s]</th><th>Mediana peak RSS [MiB]</th></tr></thead><tbody>' \
           + ''.join(rows) + '</tbody></table></div>'


style = '''
:root{color-scheme:light;--ink:#172b3b;--muted:#526778;--accent:#087f8c;--line:#d8e3e9}
*{box-sizing:border-box}body{margin:0;background:#f2f6f8;color:var(--ink);font:16px/1.6 system-ui,-apple-system,Segoe UI,sans-serif}
main{max-width:1100px;margin:32px auto;background:white;padding:46px 52px;border:1px solid var(--line);border-radius:12px}
h1{font-size:38px;line-height:1.15;letter-spacing:-1px;margin:12px 0 22px}h2{font-size:25px;margin-top:44px;border-top:1px solid var(--line);padding-top:23px}h3{font-size:18px;margin:28px 0 12px}
.tag{font-size:12px;letter-spacing:2px;font-weight:700;color:var(--accent)}.lead{font-size:20px;max-width:880px}.muted,small{color:var(--muted)}
.grid{display:grid;grid-template-columns:repeat(3,1fr);gap:14px}.metric{border:1px solid var(--line);border-radius:8px;padding:18px}.metric strong{display:block;font-size:30px;color:var(--accent)}
.callout{background:#eaf6f5;border-left:4px solid var(--accent);padding:16px 20px;margin:24px 0}.table{overflow-x:auto}table{width:100%;border-collapse:collapse;font-size:14px}th{text-align:left;background:#edf3f6}th,td{border-bottom:1px solid var(--line);padding:10px;vertical-align:top}td:nth-child(n+2){font-variant-numeric:tabular-nums;white-space:nowrap}.wrap td{white-space:normal}
code{font-family:ui-monospace,SFMono-Regular,Consolas,monospace;font-size:.9em}pre{background:#f1f5f8;padding:17px 20px;border-radius:6px;white-space:pre-wrap;overflow-wrap:anywhere;font-size:14px;line-height:1.7}.eq{padding:12px 18px;background:#f6f8fa;border-left:3px solid #9bbac7;overflow-wrap:anywhere}
li{margin:7px 0}footer{margin-top:42px;border-top:1px solid var(--line);padding-top:18px;font-size:13px;color:var(--muted)}
@media(max-width:760px){main{margin:0;padding:27px 18px;border:0;border-radius:0}h1{font-size:29px}.grid{grid-template-columns:1fr}th,td{padding:9px}}@media print{main{margin:0;border:0;padding:14px}body{background:white}h2,h3{break-after:avoid}tr,.metric,pre{break-inside:avoid}}
'''

small_case, dense_case = cases['many-small-1500'], cases['dense-60']
env = bench['environment']
body = f'''<!doctype html>
<html lang="pl"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>Grapholog 0.3 — cache i równoległość</title><style>{style}</style></head><body><main>
<div class="tag">GRAPHOLOG / WYDANIE 0.3.0 / 1 PAŹDZIERNIKA 2026</div>
<h1>Szybsze ponowne porównania projektów</h1>
<p class="lead">Wdrożono trwały cache parsowania w Pythonie i Rust oraz ograniczoną liczbę workerów w Rust. Wszystkie zmierzone warianty zachowały wynik analizy wersji 0.2 po pominięciu nowego pola statystyk <code>runtime</code>.</p>
<div class="grid"><div class="metric"><strong>{gain('many-small-1500','rust-0.2','rust-0.3-jobs4-warm')}×</strong>Rust: 1500 plików na projekt; 0.2 → 0.3, ciepły cache + 4 workery</div>
<div class="metric"><strong>{gain('dense-60','rust-0.2','rust-0.3-jobs4-warm')}×</strong>Rust: 60 plików na projekt; 0.2 → 0.3, ciepły cache + 4 workery</div>
<div class="metric"><strong>45 testów</strong>43 testy integracyjne/jednostkowe przez unittest i 2 natywne testy Rust</div></div>
<p class="muted">Przyspieszenia dotyczą dwóch korpusów syntetycznych, ponownego odczytu niezmienionych drzew i rozgrzanego cache systemu plików. Nie są gwarancją dla dowolnego repozytorium.</p>

<h2>Co zmieniono</h2>
<div class="table"><table class="wrap"><thead><tr><th>Obszar</th><th>Wdrożenie 0.3</th></tr></thead><tbody>
<tr><td>Cache</td><td><code>--cache-dir DIR</code> w obu CLI. Zapis udanych wyników parsera, bez rozwiązań zależności. Ten sam plik może użyć wpisu również pod inną ścieżką lub w drugim projekcie.</td></tr>
<tr><td>Unieważnianie</td><td>Hash treści, język, wersja analizatora, kod parsera/modelu oraz tożsamość zależności. Pliki są ponownie czytane; czas modyfikacji nie zastępuje hasha.</td></tr>
<tr><td>Aktualne zależności</td><td>Importy, kandydaci wywołań, cykle i porównania obliczane po wczytaniu wszystkich plików. Zmiana ścieżek lub korzeni importów nie zachowuje starych powiązań.</td></tr>
<tr><td>Równoległość</td><td>Rust: <code>--jobs 1..64</code>, własny parser i bufor każdego workera, wspólna kolejka plików. Sortowanie wyników przed rozwiązaniem zależności. Bez nowej zależności od Rayon.</td></tr>
<tr><td>Odporność</td><td>JSON z sumą kontrolną, walidacja typu i nagłówka, atomowa zamiana pliku. Niedostępny lub uszkodzony wpis powoduje zwykłe parsowanie. Brak pickle i wykonywania analizowanego kodu.</td></tr>
<tr><td>Domyślne zachowanie</td><td>Cache wyłączony, Rust używa jednego workera. JSON zyskał dodatkowe pole <code>runtime</code>; statystyki nie zmieniają klasyfikacji różnic ani kodu wyjścia <code>--check</code>.</td></tr>
</tbody></table></div>

<h2>Pomiary pełnego CLI</h2>
<p>Każda próba obejmuje <code>diff A B --format json</code>: inwentaryzację, odczyt i SHA-256 wszystkich zwykłych plików, parsowanie lub cache, powiązania, porównanie oraz pełną serializację JSON do <code>/dev/null</code>. Kompilacja, instalacja, tworzenie korpusu i sprzątanie cache pozostają poza pomiarem.</p>
<h3>1500 małych plików w każdym projekcie</h3>
<p>500 modułów: 500 plików TypeScript, 500 JavaScript oraz 500 plików danych. Rozmiar projektu A: {small_case['bytes_per_project']:,} bajtów. Jedna funkcja w każdym pliku TS.</p>
{table(small_case)}
<p>W Rust sam ciepły cache, przy jednym workerze, przyspiesza względem 0.3 bez cache <strong>{gain('many-small-1500','rust-0.3-off','rust-0.3-warm')}×</strong>. Cztery workery bez cache dają <strong>{gain('many-small-1500','rust-0.3-off','rust-0.3-jobs4-off')}×</strong>. Włączenie obu daje <strong>{gain('many-small-1500','rust-0.3-off','rust-0.3-jobs4-warm')}×</strong>. Python z ciepłym cache zyskuje <strong>{gain('many-small-1500','python-0.3-off','python-0.3-warm')}×</strong>.</p>
<div class="callout"><strong>Pusty cache kosztuje.</strong> W tym korpusie Rust wydłużył pierwszy przebieg z 0,507 do 0,754 s, a Python z 1,993 do 2,796 s. Zapis wielu małych wpisów przewyższa oszczędność parsowania. Dla jednorazowej analizy warto pozostawić cache wyłączony.</div>
<h3>60 plików w każdym projekcie, większe drzewa składniowe</h3>
<p>20 modułów: 20 plików TS po 100 funkcji, 20 JS i 20 plików danych. Rozmiar projektu A: {dense_case['bytes_per_project']:,} bajtów.</p>
{table(dense_case)}
<p>Przy jednym workerze ciepły cache daje względem 0.3 bez cache <strong>{gain('dense-60','rust-0.3-off','rust-0.3-warm')}×</strong> w Rust i <strong>{gain('dense-60','python-0.3-off','python-0.3-warm')}×</strong> w Pythonie. Koszt parsowania większych plików jest tu istotniejszy. Bez cache cztery workery przyspieszają Rust <strong>{gain('dense-60','rust-0.3-off','rust-0.3-jobs4-off')}×</strong>, ale podnoszą medianę szczytowego RSS z 16,9 do 28,3 MiB.</p>
<p><em>Pusty cache</em> oznacza pusty katalog na początku całego polecenia porównania. Projekt A zapełnia go przed skanem B. W obu korpusach 70% plików kodu projektu B trafia już w cache: 700 z 1000 albo 28 z 40. Dlatego nawet pierwszy przebieg większych plików może być szybszy. To nie jest pomiar odizolowanego skanu bez żadnych trafień.</p>

<h3>Metodyka i praktyczne ograniczenia</h3>
<ul><li>Jedna próba rozgrzewkowa i siedem mierzonych powtórzeń. Kolejność wariantów rotowana i odwracana. W tabelach mediana, zakres obserwowany i mediana szczytowego RSS procesu potomnego z <code>wait4</code>.</li>
<li>Cache systemu plików rozgrzany. Dla wariantów „pusty cache” nowy pusty katalog przy każdym pomiarze; dla „rozgrzany” wpisy z próby rozgrzewkowej.</li>
<li>Affinity: warianty szeregowe CPU 0; warianty 4-workerowe CPU 0–3. Limit kontenera: 8 CPU. Część zysku pochodzi z większej liczby dostępnych CPU, a nie wyłącznie z optymalizacji algorytmu.</li>
<li>{html.escape(env['cpu_model'])}; {html.escape(env['platform'])}; Python {html.escape(env['python'].split(' ')[0])}; rustc 1.98.1.</li>
<li>Python: {html.escape(', '.join(k + ' ' + v for k,v in env['packages'].items()))}. Natywne wersje gramatyk i parsera Rust zapisano w <code>rust/Cargo.lock</code>.</li>
<li>To małe syntetyczne projekty, zawierające JS/TS i dane; nie zmierzono tu dużych repozytoriów, zimnego dysku, NFS ani lokalnego Python AST. Różnica Python 0.2 → 0.3 bez cache nie jest jednokierunkowa. Nie deklarujemy przyspieszenia domyślnego skanu we wszystkich sytuacjach.</li></ul>
<p>Cache na dysku: 1300 wpisów / {small_case['cache_disk']['rust-0.3-warm']['bytes']:,} bajtów dla pierwszego korpusu; 52 wpisy / {dense_case['cache_disk']['rust-0.3-warm']['bytes']:,} bajtów dla drugiego. Wynik jednakowy dla oddzielnych cache Python i Rust w tych korpusach. Jest to rozmiar logiczny plików, bez narzutu bloków systemu plików. Drzewa, symbole i wywołania mogą zajmować więcej miejsca niż sam tekst źródłowy.</p>

<h2>Kontrakt cache</h2>
<p>Dla ustalonego silnika wynik parsera zależy od bajtów źródła, języka i jego tożsamości. Cache przechowuje ten etap, a nie gotowy graf całego projektu.</p>
<div class="eq"><code>N = SHA256(tożsamość silnika, kodu parsera/modelu i zależności)<br>K = SHA256(język || NUL || SHA256(bajty źródła))<br>cache[N, K] = udany, nierozwiązany wynik parsowania<br>graf = resolve(bieżące ścieżki, wyniki parsowania, korzenie importów)</code></div>
<p><code>SHA256(bajty źródła)</code> w kluczu jest tekstem szesnastkowym, a tożsamość jest jednoznacznie kodowana zgodnie z implementacją danego silnika. Python i Rust mają oddzielne przestrzenie. Hash zapewnia praktyczne adresowanie treści; nie jest matematycznym dowodem braku kolizji ani podpisem autentyczności cache.</p>
<p>Python uwzględnia wersję interpretera i pakietów oraz kod <code>cache.py</code>, <code>model.py</code>, <code>python_parser.py</code>, <code>tree_parser.py</code>. Rust uwzględnia wersję wydania, <code>Cargo.lock</code> oraz wbudowane bajty modułów parsera, modelu i cache. Zakładamy stabilną instalację w czasie procesu. Ręczna zmiana gramatyki bez zmiany wersji jej pakietu wymaga usunięcia cache.</p>
<p>Każdy wpis ma nagłówek z wersją formatu, przestrzenią, językiem, hashem źródła i hashem surowych danych JSON. Walidacja sprawdza też strukturę, typy, postać fingerprintów i brak starych rozwiązań importów/wywołań. Dane są zastosowane dopiero po walidacji całego wpisu. Nie zapisujemy błędów parsowania.</p>
<p>Źródła nadal są czytane w całości. Cache nie usuwa kosztu O(B) odczytu i haszowania B bajtów ani kosztu rozwiązania grafu i serializacji raportu. Pomija parsowanie trafionych plików w zamian za odczyt, walidację i deserializację wpisów. Zysk zależy od tych kosztów i od liczby trafień.</p>
<ul><li>Nowe katalogi i pliki cache mają prywatne uprawnienia na Unix. Zapis do unikalnego pliku tymczasowego kończy się atomowym zastąpieniem wpisu. Nie ma gwarancji trwałości po zaniku zasilania: cache jest odtwarzalny, nie pełni roli bazy danych.</li>
<li>Nie śledzimy symlinków wpisów cache; cache powinien należeć do użytkownika. Suma kontrolna wykrywa uszkodzenie, ale nie przeciwnika mogącego zmienić zarazem dane i sumę.</li>
<li>Limit pojedynczego wpisu: 64 MiB. Brak limitu całego cache, automatycznego sprzątania i TTL. Katalog można usunąć po zakończeniu analiz.</li>
<li>Cache wewnątrz projektu jest jawnie wyłączony ze skanu, również z <code>--all</code>. Wskazanie jako cache korzenia projektu lub jego przodka jest odrzucane.</li>
<li><code>runtime.cache</code> zawiera trafienia, chybienia, zapisy, uszkodzenia, błędy i zbyt duże wpisy. Przy równoczesnym zapełnianiu liczniki mogą zależeć od kolejności wykonania; wynik analizy pozostaje uporządkowany.</li></ul>

<h2>Sprawdzenie poprawności</h2>
<p><strong>43 + 2 testy zakończone powodzeniem.</strong> Nowe przypadki obejmują zmianę treści przy zachowaniu rozmiaru i mtime, aktualny bit wykonywalności, przeniesienie pliku z importem względnym, nowe moduły, zmianę <code>--source-root</code>, uszkodzone nagłówki i dane, niewłaściwe typy, niezgodny język, limit parsowania, Unicode w liczeniu linii, symlinki i niedostępny cache. Rust dodatkowo sprawdzono przy dwóch procesach po cztery workery, współdzielących jeden cache.</p>
<p>Benchmark porównuje pełne JSON-y 0.2 i 0.3, po usunięciu wyłącznie <code>runtime</code>: scan/diff na korpusie funkcjonalnym oraz wszystkie dziesięć wariantów obu korpusów pomiarowych. Porównanie jest wykonywane <strong>w obrębie tego samego silnika</strong>. Wszystkie warunki równości przeszły.</p>
<p>Nie utożsamiamy AST CPythona z CST Tree-sitter. Dotychczasowe różnice między silnikami, m.in. obsługa nawiasów i zapisu literałów Python, pozostają. Większa szybkość nie dowodzi pełnej analizy semantycznej. Zależności wywołań są statycznymi kandydatami; nie ma kompletnego CFG/PDG, inferencji typów ani dowodu równoważności programów.</p>

<h2>Uruchomienie i LLM</h2>
<pre><code># Rust: ponowne porównania tych samych lub podobnych projektów
chmod +x bin/grapholog-rs-linux-x86_64
./bin/grapholog-rs-linux-x86_64 diff ./A ./B --cache-dir /tmp/grapholog-cache --jobs 4

# Python: ten sam interfejs cache, jeden worker
grapholog diff ./A ./B --cache-dir /tmp/grapholog-cache

# Raport z Rust → pakiet dowodów dla LLM
./bin/grapholog-rs-linux-x86_64 diff ./A ./B --cache-dir /tmp/grapholog-cache --jobs 4 --format json &gt; /tmp/diff.json
grapholog-llm prepare /tmp/diff.json --max-chars 40000 &gt; /tmp/llm-packet.json
grapholog-llm validate /tmp/llm-packet.json /tmp/reply.json</code></pre>
<p>Pakiet LLM zachowuje dobór dowodów, limit znaków oraz rozdzielenie faktów i hipotez z 0.2. Metadane cache nie są dowodami opisującymi logikę kodu. Pełny hash raportu może się zmienić wraz ze statystykami, podczas gdy odciski plików pozostają te same. Walidator sprawdza format i referencje, nie prawdziwość twierdzeń modelu. Nie wykonano żadnego wywołania API LLM.</p>
<p>W paczce: źródła obu silników, Python wheel 0.3.0, uruchamialne archiwum <code>grapholog.pyz</code>, natywna binarka Linux x86_64 wymagająca glibc co najmniej 2.34, przykłady, testy i surowe wyniki. Dodatkowe parsery Python instaluje opcja <code>.[parsers]</code>; nie są wbudowane w wheel ani pyz. Kod Rust jako wejście <code>.rs</code> nadal ma tylko inwentaryzację i SHA-256.</p>

<h2>Odtworzenie i dalszy kierunek</h2>
<pre><code>cargo build --release --locked --manifest-path rust/Cargo.toml
python -m pip install -e '.[parsers]'
GRAPHOLOG_RUST_BIN="$PWD/rust/target/release/grapholog-rs" python -m unittest discover -s tests -v
cargo test --release --locked --manifest-path rust/Cargo.toml
python bench/cache_benchmark.py --baseline /sciezka/grapholog-0.2.0 --output /tmp/grapholog-cache-benchmark --repeats 7
python bench/cache_report.py</code></pre>
<p>Surowe wyniki: <code>results/cache/benchmark.json</code>; testy: <code>results/cache/tests.txt</code> i <code>rust-tests.txt</code>; sprawdzenie paczek: <code>package-check.txt</code>. Generator raportu czyta zapisany <code>results/cache/benchmark.json</code>; do odtworzenia dokumentu z nowego pomiaru skopiuj tam nowy plik <code>results.json</code>. Stare raporty i pomiary zachowano osobno.</p>
<p><strong>Wniosek:</strong> Rust pozostaje praktycznym silnikiem CLI w tym projekcie. Cache ma największy sens przy powtórnych analizach i droższych parserach. Wybór 2–4 workerów warto potwierdzić na własnym repozytorium, obserwując pamięć i dysk. Python pozostaje przydatny dla CPython AST, prototypowania i obsługi pakietów LLM.</p>
<p>Następne uzasadnione kroki to ograniczenie rozmiaru cache, testy na rzeczywistych repozytoriach oraz obsługa składni Rust jako wejścia. Merkle DAG, watch, MinHash/LSH, edycje AST i rozproszony harmonogram pozostają poza 0.3. Omówienie literatury, wzorów podobieństwa i zastrzeżeń do wcześniejszych uwag znajduje się w <code>RAPORT-0.1.0.html</code> oraz <code>RAPORT-0.2.0.html</code>.</p>
<footer>Wynik lokalnego eksperymentu inżynierskiego. Syntetyczne dane, jawne ograniczenia parserów, jawne warunki pomiaru. SHA-256 dostarczonej binarki Rust: <code>{env['rust_binary_sha256']['0.3']}</code>.</footer>
</main></body></html>'''
(ROOT / 'RAPORT.html').write_text(body, encoding='utf-8')
print(f'Wrote {ROOT / "RAPORT.html"}: {len(body.encode())} bytes')
