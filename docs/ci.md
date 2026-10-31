# CI: Gitea Actions on a RunPod runner

Code and pull requests live on **GitHub** (`jeevithg090/Axi0N_Meta`). CI runs on **Gitea Actions**
(gitea.com), on a self-hosted runner in a RunPod CPU pod. Every job reports back to the GitHub
commit, so PRs show `gitea/check`, `gitea/guardrails` and `gitea/ml-cpu`. CI moved here from
GitLab on 2026-10-31, when the GitLab compute credits ran out.

```
git push ──► GitHub (code, PRs, reviews)
         └─► gitea.com mirror ──► .gitea/workflows/ci.yml ──► runner on RunPod
                                                                │
              GitHub PR checks ◄── scripts/ci/github_status.py ◄┘
```

## Everyone: once per clone

```bash
make remotes GITEA=https://gitea.com/<owner>/<repo>.git
git remote get-url --all --push origin   # GitHub + Gitea, nothing else
```

From then on every `git push` goes to both hosts. You need write access to the Gitea repo; git asks
for your Gitea username and an access token (gitea.com → Settings → Applications) the first time.
Store it in your OS keychain, never in the repo.

Then work as before: push, open the PR on GitHub, `gh pr checks --watch` until every `gitea/*`
check is green. Job logs are on the Gitea repo's **Actions** tab (each GitHub check links to it).
After a squash-merge, `make sync-main` pushes `main` to Gitea.

## Repo owner: one-time setup

1. **Gitea repo.** On gitea.com create an empty **private** repository, and add teammates as
   collaborators with write access. Under Settings → Repository, make sure **Actions** is
   enabled.
2. **Secret and variable** (Gitea repo → Settings → Actions; Gitea refuses names starting with
   `GITHUB_`):
   - Secret `STATUS_TOKEN`: a fine-grained GitHub token for `jeevithg090/Axi0N_Meta` with only
     *Commit statuses: read and write*. You can reuse the one GitLab had, or make a new one.
   - Variable `STATUS_REPO` = `jeevithg090/Axi0N_Meta`.
3. **Runner token.** Gitea repo → Settings → Actions → Runners → *Create new runner* and copy the
   registration token.
4. **RunPod pod.** A CPU pod is enough (for example 4 vCPU / 16 GB, Ubuntu 22.04 image), with a
   persistent volume mounted at `/workspace` (around 20 GB, for the uv cache and the runner
   registration). Add an environment variable `GITEA_RUNNER_TOKEN` with the token from step 3 (it
   is only read on the first boot). Then, in the pod's web terminal:

   ```bash
   cd /workspace && git clone https://gitea.com/<owner>/<repo>.git rulecam-ci   # asks for a Gitea token
   bash rulecam-ci/scripts/ci/runner/bootstrap.sh
   ```

   (The clone only fetches this script. Jobs check out their own commit.)

   The script installs git, ffmpeg, Node 20, uv and Python 3.12, downloads `gitea-runner`
   (pinned), registers it with the label `rulecam` (host mode, since pods have no Docker) and starts it.
   To keep it running across pod restarts, set the pod's start command to
   `bash /workspace/rulecam-ci/scripts/ci/runner/bootstrap.sh`.
5. **Check.** The runner shows as *Idle* under Gitea → Settings → Actions → Runners. Push any
   branch; the three jobs run and their checks appear on the GitHub PR.
6. **Retire GitLab.** Remove the GitLab push URL (`make remotes GITEA=…` already does that for your
   clone), and revoke the GitHub token GitLab held if you made a new one.

## Notes

- **Cost.** The pod bills while it runs. Stop it when nobody is pushing and start it again
  before work; queued jobs wait and then run.
- **Disk.** Give the pod at least 15–20 GB: the ML extra (torch) is large. Jobs share one uv cache
  in `/workspace/.uv-cache` and hardlink from it, so parallel jobs cost little extra.
- **Capacity.** The runner takes 3 jobs at once (`RUNNER_CAPACITY` in `bootstrap.sh`). Pushing a
  newer commit to a branch cancels the older run (`concurrency` in `ci.yml`).
- **Trust.** Jobs run directly in the pod with the secrets above. Only collaborators can push to the
  Gitea repo, so only their code runs there. Keep the Gitea repo private.
- **Upgrading the runner.** Change `RUNNER_VERSION` in `bootstrap.sh` (or set it as a pod env
  var) and restart; the registration in `/workspace/gitea-runner/.runner` is reused.
