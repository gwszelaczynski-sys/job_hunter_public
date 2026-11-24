# AI Job Hunter Agent (Gemini API + GitHub Actions)

Bezserwerowy agent AI do codziennego monitorowania ofert pracy, oceny ich dopasowania do Twojego profilu zawodowego oraz automatycznego generowania spersonalizowanych wersji CV w dwóch formatach: **PDF** (gotowy do wysyłki) oraz **DOCX** (edytowalny w MS Word, LibreOffice i Google Docs).

System działa w oparciu o **GitHub Actions**, **Gemini API**, **Jina Reader** oraz **Gmaila** (IMAP/SMTP) – bez potrzeby utrzymywania własnego serwera.

## Jak działa system?

1. **Pobieranie ofert z alertów mailowych:** Portale pracy wysyłają standardowe powiadomienia na Twój adres Gmail, gdzie prosty filtr kieruje je do etykiety `JobAlerts`.
2. **Ekstrakcja i czyszczenie:** Skrypt łączy się z Gmailem przez IMAP, pobiera nieprzeczytane wiadomości (`UNSEEN`), rozwija linki śledzące (redirecty) i odsiewa już sprawdzone oferty (`data/seen_jobs.json`).
3. **Pobieranie treści ogłoszenia:**
   * Dla **LinkedIna** agent w pierwszej kolejności korzysta z publicznego endpointu `jobs-guest`, co pozwala ominąć większość ekranów logowania (*Login Wall*).
   * Dla pozostałych portali (oraz jako fallback) pobiera czystą treść ogłoszenia w Markdownie przez **Jina Reader**.
4. **Scoring ofert (`gemini-3.8-flash`):** Model ocenia każdą ofertę w skali 1–10 względem pliku `profile/master_profile.md` oraz twardych kryteriów wykluczenia, zwracając ustrukturyzowany JSON.
5. **Dopasowanie CV i generowanie PDF + DOCX (`gemini-3.8-pro` + WeasyPrint + python-docx):** Dla ofert z oceną równą lub wyższą od progu (domyślnie `7/10`) model dobiera najbardziej relevantne doświadczenia i słowa kluczowe ATS (bez halucynowania faktów), a następnie renderuje:
   * plik **`.pdf`** na podstawie `templates/cv_template.html` i `profile/candidate.json`,
   * edytowalny plik **`.docx`** z zachowaniem spójnej struktury i klauzuli RODO/GDPR dopasowanej do języka oferty (`PL`/`EN`).
6. **Archiwizacja i powiadomienie:** Bot zapisuje oba pliki CV, pełną treść oferty (`offer.md`) oraz raport dopasowania (`match_report.json`) w dedykowanym folderze `applications/RRRR-MM-DD_Firma_Rola/`, robi automatyczny commit do repozytorium i wysyła Ci zbiorczego e-maila z linkami do ofert oraz gotowych aplikacji.

### Domyślnie obsługiwane portale

* LinkedIn
* Pracuj.pl
* No Fluff Jobs
* Just Join IT
* RocketJobs
* Bulldogjob
* EY Careers (`careers.ey.com`)

## Struktura repozytorium

```text
.
├── .github/workflows/
│   └── daily_scan.yml          # Harmonogram (cron) i środowisko uruchomieniowe
├── profile/
│   ├── candidate.json          # Dane osobowe, kontaktowe, file_slug i klauzule RODO/GDPR
│   └── master_profile.md       # Twoje „źródło prawdy” (doświadczenie, metryki, kryteria)
├── prompts/
│   ├── score_job.md            # Reguły oceny ofert (output: JSON)
│   └── tailor_cv.md            # Reguły dopasowania CV bez halucynacji (output: JSON)
├── templates/
│   └── cv_template.html        # Sparametryzowany szablon HTML/CSS (Jinja2 -> PDF)
├── src/
│   └── main.py                 # Główna logika (IMAP, jobs-guest/Jina, Gemini, PDF/DOCX, SMTP)
├── data/
│   └── seen_jobs.json          # Baza przetworzonych URL-i (deduplikacja)
├── applications/               # Archiwum wygenerowanych aplikacji (PDF + DOCX + oferta + raport)
└── requirements.txt
```

## Instrukcja uruchomienia krok po kroku

### Krok 1: Utwórz własną, prywatną kopię repozytorium

> **Ważne:** Ponieważ w repozytorium będziesz przechowywać swoje dane osobowe (`candidate.json`, `master_profile.md`) oraz wygenerowane CV (`applications/`), Twoja robocza kopia **powinna być prywatna**.

1. Kliknij **Use this template** (jeśli repozytorium jest szablonem) i wybierz widoczność **Private**, albo sklonuj je lokalnie i wypchnij do nowego prywatnego repozytorium:
   ```bash
   git clone <URL_TEGO_REPOZYTORIUM> my-job-hunter
   cd my-job-hunter
   git remote set-url origin <URL_TWOJEGO_PRYWATNEGO_REPO>
   git push -u origin main
   ```
2. W ustawieniach nowego repozytorium (**Settings → Actions → General → Workflow permissions**) zaznacz **Read and write permissions** (niezbędne, aby bot mógł zapisywać wygenerowane CV i aktualizować `seen_jobs.json`).

### Krok 2: Zdobądź klucz Gemini API

1. Wejdź na [Google AI Studio](https://aistudio.google.com/) i wygeneruj klucz API (**Get API key → Create API key**).
2. **Wskazówka dotycząca limitów:**
   * **Free Tier** posiada niski dzienny limit zapytań (np. 20 zapytań/dzień dla modeli Flash), który resetuje się ok. 9:00 czasu polskiego.
   * **Pay-as-you-go (Rekomendowane):** Podpięcie karty w sekcji *Billing* znosi dzienny limit i pozwala swobodnie korzystać z modeli Flash i Pro. Przy kilkunastu ofertach dziennie miesięczny koszt to zazwyczaj ułamek dolara.

### Krok 3: Skonfiguruj konto Gmail (IMAP + SMTP)

1. Włącz weryfikację dwuetapową na koncie Google, a następnie przejdź na [myaccount.google.com/apppasswords](https://myaccount.google.com/apppasswords) i wygeneruj 16-znakowe **Hasło do aplikacji** (np. `GitHub Job Hunter`).
2. W Gmailu utwórz etykietę o dokładnej nazwie: **`JobAlerts`**.
3. Ustaw alerty mailowe na wybranych portalach pracy.
4. Utwórz w Gmailu filtr dla nadawców tych alertów, który:
   * nadaje etykietę **`JobAlerts`**,
   * *(opcjonalnie)* zaznacza **Pomiń Odebrane (Archiwizuj)**, aby alerty nie zaśmiecały głównej skrzynki,
   * **NIE zaznacza** opcji „Oznacz jako przeczytane” (agent przetwarza wyłącznie nieprzeczytane wiadomości `UNSEEN`).

### Krok 4: Dodaj sekrety w GitHubie

W swoim prywatnym repozytorium przejdź do **Settings → Secrets and variables → Actions → Repository secrets** i dodaj 3 sekrety (**New repository secret**):

| Nazwa sekretu | Wartość |
| ----- | ----- |
| `GEMINI_API_KEY` | Twój klucz API z Google AI Studio |
| `GMAIL_ADDRESS` | Twój adres e-mail w Gmailu (np. `jan.kowalski@gmail.com`) |
| `GMAIL_APP_PASSWORD` | 16-znakowe hasło do aplikacji z Kroku 3 |

### Krok 5: Spersonalizuj swój profil

Wszystkie dane osobowe są odseparowane od kodu i szablonu HTML — wystarczy zaktualizować 3 pliki:

1. **`profile/candidate.json`** – wpisz swoje imię i nazwisko, `file_slug` (używany w nazwach plików `CV_<file_slug>.pdf` i `.docx`), lokalizację, dane kontaktowe oraz klauzule RODO/GDPR:
   ```json
   {
     "full_name": "Jan Kowalski",
     "file_slug": "Jan_Kowalski",
     "location": "Warszawa, Poland (Remote / Hybrid)",
     "email": "jan.kowalski@gmail.com",
     "phone": "+48 000 000 000",
     "linkedin": "linkedin.com/in/jan-kowalski",
     "gdpr_clause_pl": "Wyrażam zgodę na przetwarzanie moich danych osobowych...",
     "gdpr_clause_en": "I hereby give consent for my personal data to be processed..."
   }
   ```
2. **`profile/master_profile.md`** – uzupełnij pełną historię zawodową, kluczowe projekty z mierzalnymi wynikami (KPI), wykształcenie, kompetencje oraz twarde kryteria (tryb pracy, lokalizacja, widełki, wykluczenia).
3. **`prompts/score_job.md`** – dostosuj kryteria wysokiej oceny (8–10/10) oraz twarde wykluczenia do swojej roli i branży.

*(Opcjonalnie: w `src/main.py` możesz zmienić próg `MIN_SCORE`, domyślnie `7`).*

### Krok 6: Pierwszy test (uruchomienie ręczne)

1. Upewnij się, że w etykiecie **`JobAlerts`** w Gmailu znajduje się przynajmniej jeden **nieprzeczytany** mail z alertem (lub wyślij sam sobie maila z linkiem do wybranej oferty).
2. Na GitHubie przejdź do zakładki **Actions → Daily Job Hunter**.
3. Kliknij **Run workflow → Run workflow**.
4. Po ok. 30–60 sekundach sprawdź:
   * logi kroku **Run Job Hunter** (zobaczysz oceny poszczególnych ofert),
   * folder `applications/` w repozytorium (dla ofert $\ge 7/10$ pojawi się podfolder z plikami `.pdf`, `.docx`, `offer.md` i `match_report.json`),
   * swoją skrzynkę odbiorczą Gmail (powinno dotrzeć podsumowanie z linkami).

## Harmonogram i codzienna praca

Workflow uruchamia się automatycznie każdego dnia zgodnie z ustawieniem `cron` w pliku `.github/workflows/daily_scan.yml` (domyślnie `0 6 * * *`, czyli ok. 7:00 rano czasu polskiego). Nazwa brancha w linkach w mailu podsumowującym jest pobierana automatycznie ze zmiennej `GITHUB_REF_NAME`.

> **Ważne:** GitHub Actions uruchamia harmonogramy (`schedule`) wyłącznie na **domyślnym branchu** repozytorium (**Settings → General → Default branch**).

### Dodawanie nowych portali pracy

Aby rozszerzyć agenta o kolejny portal:
1. Dopisz wzorzec URL oferty do listy `JOB_DOMAINS` oraz domenę portalu do `TRACKING_DOMAINS` w pliku `src/main.py`.
2. Dodaj nadawcę alertów z nowego portalu do swojego filtra `JobAlerts` w Gmailu.

## Uruchomienie lokalne (opcjonalne, do debugowania)

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt

export GEMINI_API_KEY="twoj_klucz"
export GMAIL_ADDRESS="twoj@gmail.com"
export GMAIL_APP_PASSWORD="twoje_haslo_aplikacji"

python src/main.py
```

*(Na systemie Linux wymagane są biblioteki systemowe dla WeasyPrint: `libpango-1.0-0`, `libharfbuzz0b`, `libpangoft2-1.0-0`, `libffi-dev`, `shared-mime-info`, `fonts-dejavu-core`).*

## Rozwiązywanie typowych problemów

* **Workflow przechodzi na zielono, ale nic nie przetwarza:** Sprawdź, czy w etykiecie `JobAlerts` są *nieprzeczytane* maile (`UNSEEN`) oraz czy dany link nie figuruje już w `data/seen_jobs.json`.
* **Błąd `429 RESOURCE_EXHAUSTED`:** Wyczerpałeś dzienny limit Free Tier w Google AI Studio. Podepnij kartę (Pay-as-you-go) lub przesuń godzinę crona na czas po resecie limitów (po 9:15 CET).
* **Błąd `404 NOT_FOUND` przy wywołaniu modelu:** Google regularnie aktualizuje wersje modeli. Zaktualizuj stałe `MODEL_SCORING` i `MODEL_CV` w pliku `src/main.py` (dla `MODEL_CV` skrypt posiada automatyczny fallback na `MODEL_SCORING`).
* **Odrzucony `git push` na końcu workflow:** Upewnij się, że krok commitowania w `.github/workflows/daily_scan.yml` zawiera `git pull --rebase` przed `git push` oraz że repozytorium ma nadane uprawnienia `Read and write permissions`.
