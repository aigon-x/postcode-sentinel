# CRITIQUE — PostCode Sentinel (sceptyk, nie wykonawca)

Data: 2026-10-20. Metoda: lektura README/ARCHITECTURE/DEVPOST/EVIDENCE/.gitlab-ci.yml + `sentinel/` + `tests/`;
uruchomione: `python3 -m sentinel analyze tests/fixtures/risky.diff` (exit 0, score 100),
`python3 -m pytest tests -q -p no:warnings` (28 passed), `docker run python:3.12-slim … command -v git` (NO_GIT),
`curl api.github.com/repos/aigon-x/postcode-sentinel` (public, MIT, 0 Actions runs).

## Ocena (0–100)

| Kategoria | Ocena | Uzasadnienie |
|---|---:|---|
| Technical execution | 52 | Czysty stdlib, działający parser/reguły, ale CI nie ruszy, 1 błąd CLI, testy płytkie |
| Innovation | 35 | 12 reguł regex + progi; „Duo" to mapowanie score→rekomendacja, brak nowości |
| Tech sponsora (Duo) | 8 | Brak jakiejkolwiek integracji z GitLab Duo Agent Platform; domyślnie offline |
| Jakość dowodów | 45 | EVIDENCE uczciwie oznacza braki, ale zero przebiegu CI, zero live Duo/GitLab, 1 liczba nieprawdziwa |
| Zgodność z wymogami | 25 | Repo na GitHubie, brak historii CI, brak realnego Duo, README nie uruchamia się z klona |
| **Średnia** | **33** | Ryzyko odrzucenia: **80%** (Stage One to pass/fail na „reasonably uses GitLab Duo Agent Platform") |

## 5 ulepszeń wg wpływu

1. **Podłączyć realny GitLab Duo Agent Platform** zamiast generycznego webhooka.
   Dowód atrapy: `sentinel/duo.py:49-89` to zwykły POST pod dowolny `DUO_AGENT_ENDPOINT`
   (`duo.py:33-35`), a CLI domyślnie wybiera offline (`cli.py:57,70`); brak `.gitlab/duo/*.yml`,
   brak API/SDK Duo, brak projektu GitLab. Regulamin: Stage One = pass/fail za użycie Duo.
2. **Przenieść repo na GitLab i uruchomić pipeline — dziś CI nie ma prawa się wykonać.**
   Obraz `python:3.12-slim` nie zawiera `git` (zmierzone: `NO_GIT`), a skrypt od razu woła
   `git fetch`/`git diff` (`.gitlab-ci.yml:10,22-27`) → `git: not found`. Dodatkowo repo jest na
   GitHubie, więc GitLab CI nie istnieje: `api.github.com/.../actions/runs` → `total_count: 0`.
   Wymóg: „public GitLab URL" + „CI/CD pipeline history must be visible".
3. **Naprawić Quick start — z opublikowanego repo się nie uruchamia.**
   `README.md:19` każe `cd project`, ale w korzeniu repo nie ma katalogu `project/`
   (jest `sentinel/`; zmierzone `test -d project` → NO). Wymóg: repo musi zawierać instrukcje
   pozwalające projekt uruchomić.
4. **Krytyczny sekret musi blokować, nie tylko komentować.** Diff z samym AWS key daje
   `score 40 / moderate` i rekomendację `comment_and_review` (zmierzone), bo waga 40
   (`rules.py:333`) nie osiąga progu high 55 (`scoring.py:15-18`) ani progu issue 55
   (`cli.py:16`; `duo.py:96-102`). To podważa sens produktu.
5. **Błąd CLI: `analyze` nie łapie `DuoError`.** `cli.py:58` woła `decide()` bez `try`, a `main`
   łapie tylko `DiffParseError`/`FileNotFoundError` (`cli.py:126-131`). Zmierzone z martwym
   endpointem: traceback i `EXIT=1`, wbrew `ARCHITECTURE.md:84` („CLI exit 2").
   Dodatkowo `| tee` bez `pipefail` (`.gitlab-ci.yml:30`) maskuje porażkę sentinela.

## Czy „Duo" jest atrapą? TAK

- Brak wywołania GitLab Duo Agent Platform: `duo.py` nie zna GitLaba, wysyła własny JSON
  (`task`, `score`, `level`, `findings`, `files`) na dowolny URL (`duo.py:50-66`).
- Domyślna ścieżka demo/CI to lokalny deterministyczny reasoner, jawnie oznaczony
  `offline-local-reasoner` (`duo.py:23-24,45-47`); CI nie ustawia endpointu.
- Jedyny „test live" podmienia `urlopen` (`tests/test_duo.py:52-53`); EVIDENCE przyznaje:
  „No live Duo call" (`EVIDENCE.md:146`). Zero dowodu użycia technologii sponsora.

## Czy CI faktycznie by się wykonało? NIE

- Brak `git` w obrazie (`python:3.12-slim`, zmierzone) → job pada na pierwszej komendzie.
- Repo na GitHubie → GitLab CI nie jest nigdzie podłączone; historia CI = 0 przebiegów.
- Nawet po dodaniu `git`: `git fetch origin "$TARGET"` nie gwarantuje `origin/$TARGET`,
  a fallback `git diff HEAD~1` (`.gitlab-ci.yml:26`) analizuje jeden commit, nie cały MR.
- `allow_failure: false` (`.gitlab-ci.yml:37`) to wartość domyślna — zero znaczenia.

## Co jest realnie dobre (dla uczciwości)

- 28 testów przechodzi; parser tolerantny; determinizm i „reason per finding" sensownie zaprojektowane.
- EVIDENCE nie ukrywa braku live-calli; fail-closed przy braku tokena działa (`gitlab_client.py:61-70`).
- Rozdzielenie „reguły liczą, Duo decyduje" jest obronione w ARCHITECTURE, ale w praktyce Duo nie istnieje.

## Werdykt sceptyka

Projekt to solidne lokalne narzędzie CLI, ale **nie jest zgłoszeniem hackathonu GitLab**:
technologia sponsora jest dekoracją, CI nie działa, repo jest na niewłaściwej platformie.
Bez punktów 1–2 zgłoszenie odpadnie na Stage One, niezależnie od jakości kodu.
