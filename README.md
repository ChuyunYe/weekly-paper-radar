# Weekly Paper Radar

A dependency-free static website that collects papers from arXiv and OpenAlex every Monday at 09:00 Asia/Shanghai, then filters, deduplicates, and publishes English reading briefs.

## Local usage

```bash
python3 -m unittest discover -s tests -v
python3 src/build.py --offline --week 2026-W31
python3 -m http.server 8000 -d site
```

The offline build uses `data/sample_papers.json`; it makes no network or model calls. Run `python3 src/build.py --refresh` for a live update. It saves the raw snapshot under ignored `data/raw/` and creates reproducible weekly outputs in `data/weeks/` and `site/data/`.

## Configuration, saved papers, and refreshes

- Edit `config/topics.json` to change topics, keywords, exclusions, and weekly limits. Set `manual_refresh_url` to the real GitHub Actions workflow URL after the repository is created.
- Add a DOI or arXiv ID to `config/featured.json` to pin that paper as an editor's pick.
- **Refresh now** opens the GitHub Actions workflow page. A maintainer can select **Run workflow** for an unscheduled update; a public static site cannot safely execute a collection job directly.
- **Save paper** creates a browser-local reading list. The Saved panel supports removal and JSON export; its contents do not leave the browser and do not sync across devices.
- Without `OPENAI_API_KEY`, or if the API fails, the site publishes conservative English fallbacks instead of fabricating claims.

## GitHub publishing

1. Create a public repository and push this directory.
2. Set `manual_refresh_url` to `https://github.com/OWNER/REPOSITORY/actions/workflows/weekly.yml`, rebuild the static site, and commit it.
3. In Settings → Secrets and variables → Actions, add `OPENAI_API_KEY`; `OPENAI_MODEL` is optional.
4. In Settings → Pages, select **GitHub Actions** as Source.
5. Run **Build weekly paper radar** once, or wait for the scheduled Monday run. The workflow commits the weekly archive and deploys `site/`.

Network failures from OpenAlex, arXiv, and the model service appear in Actions logs. Model failures fall back safely; collection failures fail the run rather than overwriting a prior report with empty data.
