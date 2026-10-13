# Grapholog 0.5.0


## AI Cost Tracking

![PyPI](https://img.shields.io/badge/pypi-costs-blue) ![Version](https://img.shields.io/badge/version-0.5.1-blue) ![Python](https://img.shields.io/badge/python-3.9+-blue) ![License](https://img.shields.io/badge/license-Apache--2.0-green)
![AI Cost](https://img.shields.io/badge/AI%20Cost-$1.81-orange) ![Human Time](https://img.shields.io/badge/Human%20Time-1.0h-blue) ![Model](https://img.shields.io/badge/Model-openrouter%2Fqwen%2Fqwen3--coder--next-lightgrey)

- 🤖 **LLM usage:** $1.8099 (1 commits)
- 👤 **Human dev:** ~$100 (1.0h @ $100/h, 30min dedup)

Generated on 2026-10-13 using [openrouter/qwen/qwen3-coder-next](https://openrouter.ai/qwen/qwen3-coder-next)

---



W 0.5: **duplikaty → plan ekstrakcji → tickety Planfile → Koru → zweryfikowane
artefakty → migracje projektów**. Grapholog wybiera utworzenie nowej paczki albo
wykorzystanie/uzupełnienie paczki z katalogu. Nie przenosi automatycznie kodu na
podstawie samego podobieństwa AST: wykonanie i testy są zadaniami Koru.

## Tickety, wspólne paczki i Koru

```bash
python -m pip install '.[workspace,parsers,tickets]'

# Utwórz katalog konfiguracji. Nazwa koordynatora musi odpowiadać etykiecie projektu w indeksie.
grapholog tickets config --coordinator shared-libraries -o catalog.json
# Uzupełnij packages/routes/repositories; przykład: examples/extraction/catalog.json.
grapholog tickets plan --db /tmp/grapholog.sqlite --config catalog.json -o extraction.json

# Utworzenie lokalnych ticketów w odpowiednich projektach; bez wywołania LLM.
grapholog tickets materialize extraction.json --db /tmp/grapholog.sqlite --state extraction-state.json
grapholog tickets status --state extraction-state.json --reconcile

# Podgląd przekazania zadań; --execute rzeczywiście uruchamia skonfigurowany Koru.
grapholog tickets dispatch --state extraction-state.json
grapholog tickets run --state extraction-state.json --max-steps 20 --execute

# GitHub obsługuje Planfile. Najpierw podgląd, następnie wykonanie.
grapholog tickets sync --state extraction-state.json
grapholog tickets sync --state extraction-state.json --execute
# run może synchronizować przed/po zadaniu: dodaj --sync-github.

# Zweryfikowane nowe paczki stają się katalogiem dla następnej analizy.
grapholog tickets catalog --state extraction-state.json -o next-catalog.json
```

Koru musi być zainstalowany i skonfigurowany oddzielnie. Wywołanie adaptera to
`koru ticket auto ID --project ROOT --queue grapholog --no-sync`.
`dispatch --limit N` wybiera bieżące gotowe zadania; `run --max-steps N` przechodzi
przez kolejne etapy, aż wyczerpie limit albo zabraknie zadań możliwych do wykonania.
Nie instaluje usługi w tle. Nie interpretuje wyjścia CLI Koru jako wyniku prac.

### Wybór miejsca współdzielenia kodu

Katalog wymienia **jawnie zadeklarowane wspólne paczki**, nie wszystkie projekty:

```json
{
  "schema": "grapholog.extraction-config.v1",
  "coordinator": "shared-libraries",
  "new_package_project": "shared-libraries",
  "new_package_directory": "packages",
  "queue": "grapholog",
  "packages": [{
    "id": "shared-keys", "name": "shared-keys", "project": "shared-libraries",
    "path": "src/shared_keys", "language": "python", "kind": "package"
  }],
  "routes": [{
    "package_id": "shared-keys", "language": "python",
    "path_glob": "**/keys.py", "symbol_glob": "*normalize*"
  }],
  "repositories": [
    {"project": "shared-libraries", "github": "YOUR_ORG/shared-libraries"},
    {"project": "billing", "github": "YOUR_ORG/billing"}
  ]
}
```

W `repositories` wpisz wszystkie projekty, które otrzymają tickety. To jawne
mapowanie repozytoriów; Grapholog sprawdza zgodność z konfiguracją Planfile i
istniejącym `git remote origin`. Token pozostaje w mechanizmie uwierzytelniania
Planfile (np. `GITHUB_TOKEN`); nie należy do planu, opisu issue ani stanu Graphologa.
Adapter nie tworzy repozytoriów GitHub. Nowa paczka powstaje jako katalog w
`new_package_project`, domyślnie projekcie koordynatora. Osobne repozytorium można
przygotować i wskazać w indeksie jako projekt przeznaczony na biblioteki.

Reguły wyboru:

1. Jawna reguła `routes` ma pierwszeństwo; glob porównuje względną ścieżkę i nazwę symbolu.
2. Bez reguły: wystąpienie grupy wewnątrz zarejestrowanej paczki wskazuje istniejący kod.
3. Jeden kandydat: zadania projektu/API, wykorzystania lub uzupełnienia oraz weryfikacji paczki.
4. Brak kandydata: nowa paczka o stabilnej nazwie wyprowadzonej z rodziny składniowej.
5. Kilku kandydatów: ticket wyboru/routingu, bez równoległego tworzenia następnych paczek.

`kind: artifact` obsługuje współdzielony artefakt zamiast pakietu; jest domyślny dla Bash.
Wiele rodzin skierowanych do tej samej paczki tworzy jeden zestaw zadań producenta.
Bez takiej reguły osobne rodziny mogą dawać osobne małe paczki; analiza nie rozpoznaje
samodzielnie granic domen biznesowych. Testy są dowodami, lecz domyślnie ich funkcje
nie są kandydatami do wydzielenia (`include_tests: false`). `include_shape` domyślnie
obejmuje kandydatów po normalizacji nazw. `max_groups` domyślnie ogranicza plan do
50 rodzin. Liczniki pominięć są jawne; pełny indeks pozostaje dostępny.

### Kolejność zadań i wynik pracy

Plan jest DAG: **design → package → verify → migrate → validate**. Przy niejednoznacznym
celu powstaje `route-review`. Jeden ticket migracji obejmuje wszystkie paczki potrzebne
danemu projektowi, co ogranicza równoczesne edycje tych samych plików. Opisy zawierają
lokalizacje, identyfikatory dowodów, SHA-256, zależności i kryteria akceptacji.

`TicketProposalV1` pozostaje propozycją bez uprawnień wykonawczych. Materializator
waliduje ją oficjalnym modelem Planfile, używa `create_ticket_deduplicated`, a osobno
ustawia kolejkę i `executor.kind=llm`. Nie zapisuje YAML ticketów ręcznie. Nowy projekt
otrzymuje `.planfile` przez API Planfile. Lokalne pakiety kontekstu i gramatyka GBNF
wyniku trafiają do `.planfile/grapholog/context/`. Koru otrzymuje je jako `context_files`;
Grapholog nie konfiguruje automatycznie constrained decoding w dostawcy modelu Koru.

Wykonawca zapisuje wynik według `contracts/task-result.schema.json` w wskazanej
ścieżce `.grapholog-artifacts/gt_.../result.json` i dołącza ją do `outputs.artifacts`
ticketu. Wynik wiąże `task_key`, `input_digest`, artefakty z SHA-256 i testy z logami.
Etap `verify` wymaga również nazwy/wersji paczki, mapowania eksportów dla wszystkich
rodzin oraz zgodnego przeglądu licencji z dowodem. Celem jest lokalny artefakt gotowy
do instalacji. Publikacja w PyPI/npm i utworzenie zdalnego repozytorium nie są
automatycznymi skutkami tych ticketów.

Grapholog sprawdza powiązania, obecność dowodów, deklarowane wyniki testów i hashe.
**Nie uruchamia ponownie wszystkich testów i nie dowodzi prawdziwości raportu modelu.**
Zgodność semantyki, licencji i wyników wymaga poprawnego wykonawcy oraz właściwych
testów. `needs_review`, brak wyniku, zmieniony artefakt, niezgodna paczka, anulowany
lub nieudany poprzednik blokują następny etap. Sam status `done` lub kod procesu `0`
nie wystarcza. Przed delegowaniem kopie zweryfikowanych artefaktów poprzedników są
przekazywane do `.planfile/grapholog/incoming/` konsumenta wraz z manifestem handoff.

Stan ukończenia pozostaje wyłącznie w Planfile. `extraction-state.json` przechowuje
niezmienny plan, lokalizacje checkoutów, mapowania identyfikatorów i próby delegacji.
Nie jest drugim trackerem statusów. Zależności lokalne używają `blocked_by`; między
projektami stosowana jest wyliczana etykieta `waiting:grapholog-dependencies`.
`status --reconcile` i dispatcher odświeżają ją z bieżących ticketów i artefaktów.
Do tej wieloprojektowej sekwencji używaj dispatchera Graphologa: kilka niezależnych
store'ów Planfile nie daje jednej transakcji rozproszonej.

Ponowienie materializacji przy niezmienionych wejściach zachowuje identyfikatory,
również dla ukończonych ticketów. Przerwany batch można wznowić; zmiana kodu/indeksu
przed materializacją wymaga ponownej analizy. Nieaktualne źródło wykrywane jest także
przed zadaniem używającym oryginalnych fragmentów. Timeout zatrzymuje grupę procesów
na POSIX i zapisuje niejednoznaczną próbę; Grapholog nie powtarza jej automatycznie.
Retry pozostaje jawną decyzją cyklu Planfile/Koru. Jeśli dodano etykietę
`waiting:grapholog-dispatch-review`, usuń ją dopiero po sprawdzeniu efektów próby.

Synchronizacja korzysta z backendu GitHub i dziennika potwierdzeń Planfile. Dotyczy
wyłącznie ticketów danego planu; pull pobiera konkretne przypisane issues. Opisy
otrzymują linki do zależnych ticketów w innych repozytoriach. Błąd części batcha nie
usuwa wcześniejszych powiązań; ponowienie wykorzystuje już utworzone issues.

### Kontrakty i sprawdzona integracja

CLI `tickets plan` oraz operacja `extraction_plan` we wspólnym API zwracają ten sam
kontrakt. MCP: `grapholog_extraction_plan`; REST/gRPC: dotychczasowe Query z
`operation: extraction_plan`. Tworzenie ticketów, sync i wykonanie pozostają
jawnymi komendami CLI. Nowe JSON Schema: `extraction-config`, `extraction-plan`,
`ticket-proposal`, `task-result`; dodatkowe gramatyki: `ticket-proposal.gbnf`,
`task-result.gbnf`. Duże plany wymagają mniejszego `max_groups` w transportach z
limitem wiadomości; eksport CLI nie ma limitu 8 MiB kanału gRPC.

Sprawdzone źródła: Planfile `dc427042c831b4bf0af3d18f82de071004a490c1`
(0.1.126) i Koru `cca08dc1897fe34caa17fd7a65a4a8077a195727` (0.1.461).
Dodatek `[tickets]` wskazuje ten konkretny commit Planfile. Testy używają prawdziwego
store/API/synchronizatora Planfile, symulatora granicy GitHub, rzeczywistego Koru
w trybie dry-run oraz procesów testowych dla delegacji i timeoutu. Nie wykonano
wywołania LLM ani publikacji do konta GitHub użytkownika.

Raport: `RAPORT.html`; dowody: `results/tickets/`. Wyniki 200 projektów z wydania
0.4 pozostają w `RAPORT-0.4.0.html` i `results/workspace/`.

## Analiza kodu i indeks wielu projektów

Jednorazowy wydruk w terminalu: drzewo plików, deklaracje, importy, kandydaci
wywołań, struktura sterowania, cykle zależności i duplikaty. Dwie **niezależne
implementacje**: Python i Rust. Rust nie uruchamia Pythona.

W 0.4: indeks wielu projektów w SQLite, globalne grupy funkcji, profil `logic`,
interaktywna mapa HTML, pakiety dowodów dla LLM oraz wspólny kontrakt CLI/MCP/REST/gRPC.
Warstwa indeksu i protokołów jest w Pythonie; parser można wybrać Python lub natywny Rust.
Rust pozostaje samodzielnym CLI do skanu/porównania i obsługuje `--profile logic`.

## Wiele projektów — szybki start

```bash
python -m pip install '.[workspace,parsers,mcp,grpc]'
# Budowa binarki: cargo build --release --locked --manifest-path rust/Cargo.toml
# Lub użyj bin/grapholog-rs-linux-x86_64 z paczki dla Linux x86_64/glibc.
grapholog workspace index /srv/repos --discover --db /tmp/grapholog.sqlite \
  --engine rust --rust-bin ./bin/grapholog-rs-linux-x86_64 \
  --cache-dir /tmp/grapholog-cache

grapholog workspace export --db /tmp/grapholog.sqlite --format html -o /tmp/mapa.html
grapholog workspace export --db /tmp/grapholog.sqlite --format terminal
grapholog workspace export --db /tmp/grapholog.sqlite --format ndjson -o /tmp/graf.ndjson
grapholog workspace query --db /tmp/grapholog.sqlite --operation clones \
  --params '{"cross_project":true,"limit":20}'
```

`--discover` szuka markerów projektu (`package.json`, `pyproject.toml`, `Cargo.toml`,
`.git` i innych) do głębokości 5 i zatrzymuje się na pierwszym znalezionym korzeniu.
Można wskazać kilka katalogów; do projektów bez markerów i własnych nazw służy
manifest `--manifest projects.json`. Korzenie muszą być rozłączne, aby nie liczyć
tego samego kodu dwukrotnie. Monorepo jest domyślnie jednym projektem; rozłączne
pakiety można wskazać jawnie. Konfiguracja przykładowa:

```json
{"schema":"grapholog.workspace-config.v1","projects":[
  {"id":"billing","path":"../billing"},
  {"id":"orders","path":"../orders"}
]}
```

Ścieżki manifestu są względne wobec manifestu. Przy podawaniu katalogów bez manifestu
nazwy są wyprowadzane ze ścieżek; kolizje nazw są odrzucane. Indeks zachowuje lokalne
korzenie dla pobierania źródeł; odpowiedzi API i pakiety LLM nie publikują tych korzeni.
Pełny reindeks jest jedną transakcją: nieudany skan zachowuje wcześniejszy indeks.
Nie nadpisujemy innych baz SQLite. Cache parsera jest przyrostowy, ale obecny indeks
grafu jest przebudowywany; nie jest to jeszcze watch ani analiza tylko zmienionych krawędzi.

### Filtrowanie i zakres globalnych klonów

Indeks domyślnie używa `--profile logic`. Ten sam profil działa w `scan` i `diff`
obu silników. Odrzuca dane, media, wzorce plików generowanych i zbędne katalogi
przed otwarciem zawartości. Zachowuje obsługiwany kod, testy, manifesty i licencje.
Reguły obu silników pochodzą z jednego `src/grapholog/data/logic-profile.json`.
Użytkownik może dodać `--exclude GLOB`, `.graphologignore`, albo wybrać
`--profile inventory`, jeśli dane/DSL/config również wymagają inwentaryzacji.
To jawna polityka selekcji, nie dowód, że pominięty plik nie wpływa na wykonanie.
`--all` wyłącza dotychczasowe wykluczenia i ignore, ale nie jawnie wybrany profil.
Pliki bez rozszerzenia są zachowywane do detekcji shebang, jeśli mają bit wykonywalności.

`exclusions` udostępnia ścieżki i powody pominięć. Katalog odcięty z poddrzewem jest
liczony jako jeden wpis — nie zgadujemy liczby ukrytych pod nim plików ani bajtów.
Znane nieobsługiwane języki, np. `.rs`, są wykazywane jako `unsupported-language`.
Testy nie są usuwane. Wartości `complete_within_policy` odnoszą się wyłącznie do
przyjętej polityki i zakresu parserów, nie do semantyki całego systemu.

Grupy globalne obejmują **funkcje i metody**: zgodną składnię z pominięciem nazwy
samej deklaracji (`function-body`) oraz kandydatów po normalizacji identyfikatorów
(`function-shape`). Małe deklaracje poniżej `--min-nodes 12` nie tworzą grup.
Klasy nie są grupowane jako funkcje. Hashy różnych parserów/języków nie mieszamy.
Nie wykrywamy jeszcze dowolnych podobnych fragmentów wewnątrz funkcji ani klonów
między różnymi językami. Dotychczasowy `diff` nadal ma wykrywanie kopii całych plików.

Każda grupa ma jedną krawędź do każdego wystąpienia, zamiast wszystkich par kopii.
SQLite grupuje po indeksowanych odciskach; nie porównujemy każdej pary projektów.
Ranking używa liczby projektów i `SUM(nodes)-MAX(nodes)` dla grupy. To heurystyka
powtarzanej składni, nie przewidywana oszczędność linii czy uzasadnienie refaktoryzacji.
Zakresy zagnieżdżonych deklaracji mogą się nakładać. Importy/wywołania rozwiązujemy
w obrębie projektu; połączenia do zewnętrznych pakietów pozostają jawnie nierozwiązane.

### Jeden kontrakt, cztery interfejsy

```bash
grapholog contracts --dir /tmp/grapholog-contracts
grapholog serve mcp --db /tmp/grapholog.sqlite
grapholog serve rest --db /tmp/grapholog.sqlite --port 8765
grapholog serve grpc --db /tmp/grapholog.sqlite --port 8766
```

Adaptery udostępniają ten sam zestaw operacji: `summary`, `projects`, `clones`,
`members`, `nodes`, `neighbors`, `exclusions`, `evidence`, `context`. Wyniki mają
`schema`, `snapshot_id`, `operation`, `data`. Strony list zawierają `offset`,
`limit`, `total`, `truncated`; limit jednej strony to 200. Porównuj `snapshot_id`
przy łączeniu stron, ponieważ reindeks może zatwierdzić nowy stan pomiędzy zapytaniami.
Jedno zapytanie widzi jeden zatwierdzony stan SQLite. Interfejsy czytają wskazany
indeks; nie przyjmują dowolnych ścieżek do skanu, komend shell ani modyfikacji kodu.

| Interfejs | Użycie wspólnego kontraktu |
|---|---|
| CLI | `workspace query --db FILE --request request.json` lub `--operation OP --params JSON`; `--request -` czyta stdin |
| REST | `POST /v1/query`; `GET /v1/summary`, `/v1/schema/{name}`, `/v1/grammar/{name}`, `/v1/openapi.json` |
| MCP | oficjalny Python SDK, stdio; narzędzia `grapholog_summary`, `grapholog_clones`, itd.; inputSchema/outputSchema i structuredContent; schematy/gramatyki jako resources |
| gRPC | usługa `grapholog.v1.Grapholog`, `Query` i `Contract`; `.proto` transportuje te same UTF-8 JSON w polu bytes, a JSON Schema waliduje semantykę payloadu |

REST i gRPC nasłuchują wyłącznie na `127.0.0.1`. Opcjonalny wspólny token:
zmienna `GRAPHOLOG_API_TOKEN`; REST nagłówek `Authorization: Bearer ...`, gRPC
metadata `authorization`. MCP stdio działa jako lokalny proces klienta. Nie ma
jeszcze TLS ani publicznego hostingu, wielodostępności czy MCP Streamable HTTP.
Token nie jest wypisywany. REST odrzuca obce Host/Origin i nie włącza CORS.

Kontrakty są wersjonowane niezależnie od wydania: JSON Schema draft 2020-12,
OpenAPI 3.1.1 i protobuf v3. Zewnętrzna nazwa GBNF to **GGML BNF**; nie wprowadzamy
odrębnego, nieustalonego standardu „GGNF”. GBNF służy do generowania poprawnej
składni zapytań/propozycji, a graf jest JSON/NDJSON zgodnym ze schematem.

### LLM i wydzielanie bibliotek

```bash
# Weź group_id z operacji clones:
grapholog workspace context --db /tmp/grapholog.sqlite --group-id g_ID \
  --include-source --max-chars 40000 -o /tmp/context.json
# Przekaż messages i reply_schema albo grammar do wybranego LLM.
grapholog workspace validate /tmp/context.json /tmp/propozycje.json
```

Pakiet zawiera grupy, projekty, pliki kontekstowe (w tym licencje), funkcje z liniami,
SHA-256, importy, strukturę logiczną oraz przychodzące i wychodzące krawędzie wywołań.
Opcjonalne źródło jest odczytywane z aktualnego, zwykłego pliku i weryfikowane względem
hasha w indeksie; po zmianie pliku zwracamy `changed`, bez nieaktualnego wycinka.
Wycinek symbolu może nie obejmować dekoratorów poza jego zakresem — pełniejszy kontekst
pobierz przez `evidence` dla pliku. Limit odczytu źródła wynosi 8 MiB, wycinka do 16000
znaków; listy dowodów są ograniczone i mają jawne liczniki pominięć.

Kontekst wybiera wystąpienia naprzemiennie z różnych projektów i grup. Limit znaków
obejmuje oba messages, także umieszczony w nich reply_schema; gramatyka jest osobnym
wejściem dekodera i nie należy do tego limitu. To nie dokładny limit tokenów modelu.
W razie małego budżetu liczba dowodów maleje; większy kontekst wymaga kolejnych zapytań.

Propozycja ma `decision` (`extract_library`, `parameterize`, `keep_local`,
`needs_review`), nazwę biblioteki, uzasadnienie, `evidence_ids`, ryzyka, testy i
niekalibrowane `confidence`. Walidacja sprawdza wersję, stan indeksu, dozwolone
identyfikatory i przynależność dowodów do grupy; wydzielenie/parametryzacja wymaga
co najmniej dwóch różnych wskazanych wystąpień. Nie dowodzi prawdziwości propozycji,
czystości funkcji ani zgodności licencji i nie wykonuje żadnej zmiany.

GBNF jest generowany dla zamkniętego podzbioru schematów. Narzuca kanoniczną kolejność
pól oraz obecność także pól opcjonalnych. JSON Schema przyjmuje dowolną kolejność.
Zakresy liczb całkowitych, wzorce identyfikatorów, unikalność i zależności między
polami nadal wymagają walidacji po generacji. Konkretne ID w pakiecie LLM są
ograniczone enumami. Nie obiecujemy, że constrained decoding zapewnia poprawną decyzję.
Dostarczone badanie nie wykonuje wywołania modelu.

Przykłady w `results/workspace/`: `example-llm-context.json` zawiera gotowe messages,
reply_schema i grammar, `example-proposal-manual.json` jest ręcznie napisaną odpowiedzią,
a `example-proposal-validation.json` pokazuje wynik jej walidacji. `example-graph.json`
i `example-terminal.txt` przedstawiają te same trzy projekty z `examples/workspace/`.

Eksport JSON/NDJSON zawiera cały zindeksowany graf. HTML/DOT/terminal to widok
ograniczony do `--limit` grup i 40 wystąpień na grupę, z jawnym licznikiem pominięć.
Mapa HTML jest samodzielna, działa offline, ma filtry i widok zależności wybranej grupy.
Diagram pokazuje do 12 wystąpień, tabela do 40; lista projektów grupy jest kompletna.
Wyszukiwanie w HTML obejmuje dane tego eksportu; pełne zapytania wykonuj na indeksie.

### Powtórzenie testu wielu projektów

```bash
python bench/workspace_benchmark.py --output /tmp/grapholog-workspace-bench \
  --projects 200 --repeats 3 --rust ./rust/target/release/grapholog-rs
```

Wyniki i metodologię wydania 0.4 zawierają `RAPORT.html` oraz `results/workspace/`.
Kontrolę zainstalowanego koła i budowę zipapp powtarza `python bench/package_check.py`
w osobnym środowisku z zainstalowanym kołem 0.4 i dodatkami workspace/parsers/mcp/grpc.
Poniższe sekcje opisują zachowany interfejs jednego/dwóch projektów i cache z 0.3.

W 0.3: opcjonalny trwały cache parsowania w obu silnikach oraz `--jobs` w Rust.
Pliki źródłowe są zawsze odczytywane i haszowane. Wersja 0.3 zachowuje
optymalizacje fingerprintów, indeksu Jaccarda, JSON i kontekstu LLM z 0.2.
Pomiary 0.3 oraz zakres gwarancji opisuje `RAPORT-0.3.0.html`.

## Szybki start

Python 3.10+:

```bash
python3 -m venv .venv
. .venv/bin/activate
python -m pip install '.[parsers]'
grapholog scan /sciezka/projekt --all --detail full
grapholog diff /sciezka/projekt-A /sciezka/projekt-B
```

Instalacja bez `[parsers]` wystarcza do analizy Python AST i inwentaryzacji
pozostałych plików. Brak parsera jest jawnie oznaczany. Plik `grapholog.pyz`
uruchamia się też przez `python3 grapholog.pyz ...`; dodatkowe parsery muszą
być zainstalowane w używanym interpreterze. Archiwum `.pyz` nie zawiera ich.

Rust, kompilacja ze źródeł (wymaga Cargo, kompilatora C, dostępu do crates.io):

```bash
cargo build --release --locked --manifest-path rust/Cargo.toml
./rust/target/release/grapholog-rs scan /sciezka/projekt --all --detail full
./rust/target/release/grapholog-rs diff /sciezka/A /sciezka/B
```

W paczce wydaniowej jest też `bin/grapholog-rs-linux-x86_64` z wbudowanymi
gramatykami. To binarka Linux x86_64/glibc, nie uniwersalny plik dla macOS,
Windows i ARM. Po rozpakowaniu ZIP może być potrzebne `chmod +x bin/grapholog-rs-linux-x86_64`.

```bash
./bin/grapholog-rs-linux-x86_64 scan ./projekt --all
./bin/grapholog-rs-linux-x86_64 diff ./A ./B --format json > /tmp/diff.json
```

## Widoki i eksport

```bash
grapholog ./projekt                         # skrót: scan
grapholog scan ./projekt --detail files     # samo kompletne drzewo
grapholog scan ./projekt --detail full      # warunki, pętle, return, call
grapholog scan ./projekt --format json -o /tmp/project.json
grapholog scan ./projekt --format dot -o /tmp/project.dot
grapholog diff ./A ./B --format json -o /tmp/diff.json
grapholog diff ./A ./B --check               # 1 przy różnicach, 0 bez różnic
grapholog scan ./projekt --strict           # 2 przy niepełnej analizie kodu
grapholog scan ./projekt --ascii            # terminal bez Unicode
grapholog scan ./projekt --exclude '*.min.js' --source-root src
```

Rust ma te same powyższe opcje oraz `--jobs 1..64`. Tylko Python ma `--color auto|always|never`
oraz pomocnicze polecenie `grapholog-llm`. Rust emituje czytelny tekst bez ANSI.
`--output` odmawia nadpisania istniejącego pliku. Zapisuj raport poza skanowanym
drzewem, szczególnie przy użyciu przekierowania `>` (shell tworzy plik przed startem skanu).

## Zakres

| Rodzaj pliku | Python | Rust |
|---|---|---|
| Python `.py`, `.pyi` | wbudowane `ast`, uwzględnia deklarację kodowania | Tree-sitter CST; UTF-8 |
| JS/JSX, TS/TSX | opcjonalne Tree-sitter | Tree-sitter w binarce |
| PHP, Bash/sh | opcjonalne Tree-sitter | Tree-sitter w binarce |
| Inne, w tym kod Rust `.rs` | drzewo, rozmiar i SHA-256 | drzewo, rozmiar i SHA-256 |
| Binarne / ponad limit parsera | SHA-256 i status | SHA-256 i status |
| Symlink | opis samego linku, bez śledzenia | opis samego linku, bez śledzenia |

Wydanie 0.4 nie ma jeszcze adaptera do analizy składni `.rs`; język
implementacji silnika nie oznacza automatycznie obsługi wszystkich języków wejściowych.

Domyślnie wykluczane są `.git`, `node_modules`, `vendor`, `target`, wirtualne
środowiska, cache i katalogi wynikowe. Wszystkie wykluczone wpisy są wymieniane.
`--all` wyłącza te reguły oraz `.graphologignore`; nadal obowiązuje jawne
`--exclude`. Domyślny limit parsowania to 2 MiB na plik, zmiana przez
`--max-bytes N`; `0` wyłącza limit. Cały regularny plik jest haszowany także
po przekroczeniu limitu. `--min-nodes 12` ogranicza szum w grupach składni/funkcji.

`.graphologignore` to proste wzorce glob, po jednym na linię, `#` rozpoczyna
komentarz. Nie jest implementacją `.gitignore`: brak negacji `!`, brak reguł
zagnieżdżonych. Projektów nie uruchamiamy ani nie importujemy jako kod.

## Jak czytać wynik

- `=`: zgodny SHA-256 bajtów i typ pliku. SHA-256 służy za praktyczny fingerprint;
  program nie wykonuje dodatkowej weryfikacji bajt po bajcie grup z tym samym hashem.
- `S / syntax-equal`: zgodne reprezentacje składni po usunięciu pozycji/komentarzy
  zależnie od parsera; zachowujemy operatory i wartości literałów.
- `N?`: kandydat zmiany nazw po normalizacji identyfikatorów według kolejności
  wystąpień. To heurystyka, nie pełne rozwiązywanie zakresów ani dowód alfa-równoważności.
- `R= / RS`: jednoznaczna para treści na innej ścieżce. Nie odtwarzamy historii Git.
  Przy wielu identycznych kopiach nie zgadujemy parowania.
- `~`: zmiana; `+` / `-`: dodanie / usunięcie; `X`: bit wykonywalności;
  `!`: brak wiarygodnego odczytu.
- `?` przy linku: statyczny kandydat. Metody dynamiczne, aliasy, monkey-patching,
  re-eksporty i nadpisane nazwy mogą zmienić rzeczywiste wywołanie.
- `decisions`: licznik wybranych konstrukcji składniowych; to **nie** pełne CFG
  i **nie** gwarantowana złożoność cyklomatyczna McCabe'a.

Grupy funkcji `=*` ignorują nazwę samej deklaracji, zachowują jej pozostałą
strukturę, w tym nazwy parametrów. Podobieństwo przybliżone to Jaccard zbiorów
5-gramów serializowanego drzewa (hash 64-bitowy, uzyskany przez skrócenie SHA-256).
Możliwe kolizje oraz utrata globalnej kolejności i liczności oznaczają, że wynik
to kandydat do sprawdzenia. Porównania przybliżone dotyczą tylko niedopasowanych
plików usuniętych/dodanych. `--threshold 0.7`, `--max-pairs 20000`; osiągnięcie
limitu jest raportowane. Nie jest to implementacja Winnowing, MinHash ani GumTree.

W 0.2 indeks odwrócony, filtr długości i filtr prefiksu eliminują pary, które
nie mogą osiągnąć progu Jaccarda. Nie zmieniają zbiorów fingerprintów.
`similarity_search.considered` zachowuje znaczenie budżetu par z 0.1;
`verified` podaje liczbę obliczonych przecięć, a `pruned` — par wykluczonych
na podstawie bezpiecznego ograniczenia. `truncated: true` nadal oznacza
niepełne wyszukiwanie wskutek `--max-pairs`. Filtr nie daje gwarancji
podkwadratowego czasu dla każdego wejścia ani równoważności semantycznej.

## Cache i równoległość (0.3)

Cache jest domyślnie wyłączony. Włącz go dla powtarzanych analiz, wskazując
osobny katalog, najlepiej poza porównywanymi projektami:

```bash
./bin/grapholog-rs-linux-x86_64 diff ./A ./B --cache-dir /tmp/grapholog-cache --jobs 4
./bin/grapholog-rs-linux-x86_64 scan ./projekt --cache-dir /tmp/grapholog-cache --jobs 4
# Python ma ten sam cache interfejsu CLI; skanuje jednym workerem:
grapholog diff ./A ./B --cache-dir /tmp/grapholog-cache
```

Cache zapisuje wyłącznie udane wyniki parsowania: odciski, deklaracje,
niepowiązane importy/wywołania i strukturę logiczną. Przy każdym skanie
odczytywane są aktualne bajty, typ, rozmiar i bit wykonywalności. Klucz opiera
się na SHA-256 treści i języku. Przestrzeń cache jest dodatkowo wyznaczana przez
wersję silnika, kod parsera/modelu i zależności: w Pythonie wersję interpretera
oraz pakietów parserów, a w Rust również `Cargo.lock`. Zmiany własnych gramatyk
wymagają poprawnego wersjonowania pakietów; cache zakłada stabilne instalacje
analizatora i zależności podczas działania procesu.

Importy, wywołania, cykle i porównania są obliczane ponownie dla aktualnej
struktury projektu i `--source-root`. Dlatego przeniesienie pliku, dodanie
modułu lub zmiana korzeni importów nie używa powiązań ze starego drzewa.
Cache Python i Rust ma oddzielne przestrzenie: AST CPythona i CST Tree-sitter
pozostają różnymi reprezentacjami.

Wpis ma nagłówek JSON z wersją i sumą kontrolną oraz jeden wiersz danych JSON.
Zapis odbywa się do pliku tymczasowego i kończy atomową zamianą nazwy. Błąd
cache, niezgodny nagłówek, uszkodzona suma lub niewłaściwy typ danych powodują
ponowne parsowanie. Cache nie używa pickle i nie wykonuje kodu projektu.
Sprawdzenie sumy zabezpiecza przed przypadkowym uszkodzeniem; cache powinien
pozostawać prywatnym katalogiem użytkownika, a nie źródłem zaufanych danych
przekazywanych przez inne osoby.

`--max-bytes` i obsługa błędów obowiązują także przy trafieniu w cache. Nie są
zapisywane błędne/niepełne wyniki parsowania. Wpisy ponad 64 MiB są pomijane.
Nie ma jeszcze limitu całkowitego rozmiaru ani automatycznego usuwania starych
wersji; katalog jest odtwarzalny i można usunąć go po zakończeniu skanów.
Pierwszy skan ponosi koszt zapisu, więc cache może być niekorzystny dla
jednorazowej analizy wielu małych plików.

Jeżeli katalog cache znajduje się w projekcie, jest wykluczany z inwentarza
również z `--all`; wykluczenie jest jawnie wykazane. Cache nie może wskazywać
korzenia analizowanego projektu ani jego katalogu nadrzędnego. Użyj osobnego
katalogu. Błąd samego cache jest raportowany w `runtime.cache`, a nie jako
błąd pokrycia analizy; działający parser nadal może zakończyć `--strict` kodem 0.

Rust domyślnie używa jednego workera. `--jobs N` ustala ograniczenie od 1 do 64;
faktyczna liczba nie przekracza liczby regularnych plików (przy pustym wejściu
metadane wskazują 1). Każdy worker ma własne parsery i bufor odczytu. Wyniki są
sortowane przed rozwiązywaniem zależności. Więcej workerów zwiększa możliwość
zużycia pamięci i nie musi pomagać przy małych plikach lub wolnym dysku.

JSON zawiera dodatkowe pole `runtime`: liczbę workerów oraz statystyki
`hits`, `misses`, `writes`, `invalid`, `errors`, `skipped_large`, `issues`.
Pole opisuje przebieg i może różnić się między zimnym/ciepłym cache lub
współbieżnymi uruchomieniami. Przy porównaniu powtarzalności samej analizy
pomiń `runtime`; odciski plików i wynik `--check` nie zależą od statystyk cache.
W terminalu statystyki pojawiają się po włączeniu cache lub wielu workerów.

## Ograniczenia rozwiązywania zależności

Python: korzenie `.` i `src`, importy względne, proste aliasy; własne korzenie
przez powtarzane `--source-root`. JS/TS: ścieżki względne, typowe rozszerzenia,
`index`, niektóre importy `require`. Nie odczytujemy `tsconfig.paths`, map
bundlera, `package.json.exports`, ani całego środowiska modułów.
PHP: literalne include/require, także `__DIR__ . '/plik.php'`; bez Composer
autoload. Bash: literalne `source`/`.`; rzeczywisty katalog roboczy może być inny.
Wywołania PHP/Bash przez dołączone pliki pozostają zwykle nierozwiązane.
Deklaracje/importy mogą być warunkowe; nie śledzimy wykonania warunków.
Nie implementujemy inferencji typów, przepływu danych ani kompletnego CFG/PDG.
Węzły logiczne mają zakres syntaktyczny, nie pełną semantykę wykonania dekoratorów,
wartości domyślnych i inicjalizacji klas.

Unicode w nazwach i źródłach jest obsługiwany. Rust wykorzystuje stratną konwersję
dla nie-UTF-8 nazw plików/linków; taki nietypowy zestaw należy analizować Pythonem.
W źródłach Rust wymaga UTF-8, a Pythonowy `ast` potrafi respektować PEP 263.
Skan nie jest atomowym snapshotem całego systemu plików. Dla odtwarzalnych wyników
analizuj nieruchome checkouty; proste zmiany rozmiaru/czasu zapisu podczas odczytu są oznaczane.

## Połączenie z LLM

```bash
grapholog diff ./A ./B --format json > /tmp/diff.json
grapholog-llm prepare /tmp/diff.json --max-chars 40000 > /tmp/llm-packet.json
# Przekaż messages oraz response_schema do wybranego lokalnego lub zdalnego LLM.
# Zapisz obiekt JSON odpowiedzi jako /tmp/reply.json.
grapholog-llm validate /tmp/llm-packet.json /tmp/reply.json
```

Eksporter działa również na JSON z Rusta. Dobiera zmiany, grupy duplikatów i
sąsiedztwo importów o jednym skoku. Ogranicza liczbę znaków wiadomości, raportuje
pominięte rekordy, przydziela identyfikatory dowodów. Nie zawiera surowych plików,
lecz nazwy/sygnatury/importy mogą nadal ujawniać treść projektu. Limit znaków
nie jest dokładnym limitem tokenów wybranego modelu.

W 0.2 typy dowodów są wybierane naprzemiennie: zmiana, plik, grupa duplikatów.
Koszt każdego rekordu jest liczony raz, bez ponownej serializacji całego
dotychczasowego kontekstu. Zbyt duży rekord pliku może zostać zastąpiony
jawnym podsumowaniem (`details_omitted`, liczniki). Pole `summarized_records`
raportuje takie przypadki. To deterministyczna heurystyka doboru kontekstu,
bez twierdzenia o optymalności lub gwarancji jakości odpowiedzi LLM.

Odpowiedź rozróżnia `fact` i `hypothesis`, zawiera `evidence_ids`, ocenę
`confidence` i `suggested_test`. Walidator sprawdza format i odwołania, **nie**
prawdziwość semantyczną. W dostarczonym badaniu żaden LLM nie został wywołany.
Testy propozycji należy uruchamiać w przeznaczonym do tego środowisku; narzędzie
nie wykonuje poleceń zwróconych przez model.

## Testy i powtórzenie pomiarów

```bash
python -m pip install -e '.[parsers]'
GRAPHOLOG_RUST_BIN="$PWD/rust/target/release/grapholog-rs" \
  python -m unittest discover -s tests -v
PYTHONPATH=src python bench/run.py --rust rust/target/release/grapholog-rs \
  --output /tmp/grapholog-benchmark --sizes 10,100,500 --repeats 7
cargo test --release --locked --manifest-path rust/Cargo.toml
# Po rozpakowaniu paczki 0.1.0 do /tmp/grapholog-0.1.0:
python bench/optimize.py --baseline /tmp/grapholog-0.1.0 \
  --output /tmp/grapholog-optimization --repeats 7
```

Benchmark wymaga Linuxa i kompilatora `cc`; używa małego procesu C i `wait4`
do pomiaru rzeczywistego szczytowego RSS dziecka. Obejmuje pełny CLI, skan,
parsowanie, indeksowanie, porównanie i serializację JSON, wyjście do `/dev/null`.
Pomija kompilację i instalację. Kolejność Python/Rust jest naprzemienna, dane
w cache, jeden rdzeń przypisany przez affinity. Generator tworzy 3 pliki na moduł.
W `results/` są próbki surowe, wersje środowiska, raport testów i przykładowe wyniki.

Zobacz `RAPORT-0.3.0.html` po pomiary 0.3, opis cache i równoległości. Raporty
wcześniejszych wydań zachowano osobno. To badanie na korpusach syntetycznych,
nie walidacja naukowa kompletnego detektora ani ogólny ranking języków.
Wersja 0.3 nie implementuje watch, edit-script AST, MinHash/LSH, Merkle DAG,
pełnego grafu przepływu danych, rozproszonego harmonogramu ani klienta API LLM.

Aby powtórzyć porównanie 0.2 i 0.3 (Linux, cc, gramatyki Python):

```bash
python bench/cache_benchmark.py --baseline /ścieżka/grapholog-0.2.0 \
  --output /tmp/grapholog-cache-benchmark --repeats 7
```

Benchmark porównuje wyłączony cache, pusty cache, rozgrzany cache oraz Rust z
jednym i czterema workerami. W każdym wariancie sprawdza zgodność całego wyniku
analizy z 0.2 po usunięciu wyłącznie metadanych `runtime`. Pusty cache oznacza
cache aplikacji; cache systemu plików pozostaje rozgrzany.

## Przykład wieloprojektowy z paczki

```bash
grapholog workspace index --manifest examples/workspace/projects.json --db /tmp/demo.sqlite
grapholog workspace export --db /tmp/demo.sqlite --format html -o /tmp/demo.html
```

`examples/workspace/` zawiera trzy projekty z funkcjami o zgodnej składni i po zmianie
nazw parametrów. Są to dane demonstracyjne. `results/workspace/map-200-projects.html`
pokazuje większy korpus. Generator odtwarza wszystkie jego 200 projektów.

Przykład REST (opcjonalny token dodaj w nagłówku Authorization):

```bash
curl http://127.0.0.1:8765/v1/query -H 'Content-Type: application/json' \
  -d '{"schema":"grapholog.request.v1","operation":"clones","params":{"limit":20}}'
```

Przykład klienta gRPC po instalacji `.[grpc]`:

```python
import json, grpc
from grapholog.proto import grapholog_pb2 as pb, grapholog_pb2_grpc as rpc
with grpc.insecure_channel("127.0.0.1:8766") as channel:
    client = rpc.GraphologStub(channel)
    request = {"schema":"grapholog.request.v1","operation":"summary","params":{}}
    response = client.Query(pb.JsonRequest(json_utf8=json.dumps(request).encode()), timeout=10)
    print(json.loads(response.json_utf8))
```

Klient MCP uruchamia polecenie `grapholog serve mcp --db /absolutna/sciezka/index.sqlite`
jako proces stdio. Wybierz interpreter z dodatkiem `[mcp]`; stdout zawiera wyłącznie
wiadomości protokołu. Schematy, gramatyki i `grapholog.proto` znajdują się także
w katalogu `contracts/` paczki.


## License

Licensed under Apache-2.0.
