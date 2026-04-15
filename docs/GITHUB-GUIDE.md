# ScoreStreamArr — GitHub & Version Management Guide

This guide covers everything you need to manage ScoreStreamArr on GitHub — from first push to publishing releases, managing beta vs. stable builds, and keeping documentation current.

---

## Table of Contents

1. [First Time Setup](#1-first-time-setup)
2. [Understanding the Repository Structure](#2-understanding-the-repository-structure)
3. [Daily Git Workflow](#3-daily-git-workflow)
4. [Beta vs. Stable — How Versioning Works](#4-beta-vs-stable--how-versioning-works)
5. [Publishing a Release](#5-publishing-a-release)
6. [Managing the Docker Images on ghcr.io](#6-managing-the-docker-images-on-ghcrio)
7. [Writing Release Notes](#7-writing-release-notes)
8. [Maintaining the User Guide](#8-maintaining-the-user-guide)
9. [GitHub Wiki Setup](#9-github-wiki-setup)
10. [Troubleshooting Common Git Problems](#10-troubleshooting-common-git-problems)

---

## 1. First Time Setup

### Install Git

**Windows:** Download from https://git-scm.com — install with all defaults.  
**Mac:** Run `git --version` in Terminal; it will prompt you to install if missing.  
**Linux/Proxmox:** `sudo apt install git`

### Configure your identity (one time only)

```bash
git config --global user.name "Your Name"
git config --global user.email "you@email.com"
```

### Create a GitHub account

Go to https://github.com and sign up. Your username becomes part of all your image URLs (e.g. `ghcr.io/YOURUSERNAME/scorestreamarr-pro-api:latest`), so pick something you're happy with.

### Create the repository on GitHub

1. Click the **+** in the top right → **New repository**
2. Name it: `scorestreamarr-pro`
3. Set it to **Public** (required for free ghcr.io image hosting)
4. **Do NOT** check "Initialize with README" — we'll push our own files
5. Click **Create repository**

GitHub will show you a page with commands. Keep that tab open.

### Push your code for the first time

Open a terminal in the folder where you extracted `scorestreamarr-pro.zip`:

```bash
cd scorestreamarr

# Initialize git in this folder
git init

# Tell git to track all files
git add .

# Create your first snapshot (commit)
git commit -m "feat: initial release v0.1.0-beta"

# Connect your local folder to GitHub
git remote add origin https://github.com/YOURUSERNAME/scorestreamarr-pro.git

# Push everything to GitHub
git push -u origin main
```

> **Tip:** If git asks for your GitHub password, use a **Personal Access Token** instead of your actual password.
> GitHub Settings → Developer Settings → Personal Access Tokens → Tokens (classic) → Generate new token.
> Check the `repo` and `packages` scopes. Save the token — you only see it once.

### Enable GitHub Actions (automatic Docker builds)

1. On your repo page, click **Actions** tab
2. Click **I understand my workflows, go ahead and enable them**

That's it. The workflows in `.github/workflows/` will now run automatically.

### Allow GitHub Actions to publish packages

1. Go to your GitHub **Settings** (account level, top right) → **Actions** → **General**
2. Under "Workflow permissions" → select **Read and write permissions**
3. Save

---

## 2. Understanding the Repository Structure

```
scorestreamarr-pro/
├── .github/
│   └── workflows/
│       ├── beta.yml       ← Runs on every push to main → builds :beta Docker image
│       └── release.yml    ← Runs when you publish a version tag → builds :latest image
│
├── scorestreamarr/           ← All the actual application code
│   ├── api/               ← Python Dispatcharr integration
│   ├── renderer/          ← Headless Chrome capture
│   ├── ffmpeg/            ← HLS encoding
│   ├── nginx/             ← Web server
│   ├── config/
│   │   └── config.json    ← User-editable channel/profile settings
│   ├── scoreboard.html    ← The scoreboard UI
│   └── docker-compose.yml ← How all containers connect
│
├── docs/
│   ├── GITHUB-GUIDE.md    ← This file
│   ├── USER-GUIDE.md      ← End-user installation & configuration guide
│   └── RELEASE-NOTES/     ← Per-version release notes
│       ├── v0.1.0-beta.md
│       └── v0.2.0-beta.md
│
├── CHANGELOG.md           ← Developer-facing history of all changes
└── README.md              ← The front page of your GitHub repo
```

---

## 3. Daily Git Workflow

When you make changes to ScoreStreamArr using vibe coding or manually, here's the process to get those changes saved and published.

### The three commands you'll use constantly

```bash
# 1. See what files you've changed
git status

# 2. Stage (select) the changes you want to save
git add .                    # Add everything
git add scorestreamarr/api/app.py   # Or add one specific file

# 3. Commit — save a snapshot with a description
git commit -m "fix: channel numbers not updating after config change"

# 4. Push — send to GitHub
git push
```

### Writing good commit messages

Use this format: `type: short description`

| Type | When to use |
|------|-------------|
| `feat:` | New feature added |
| `fix:` | Bug fixed |
| `docs:` | Documentation only |
| `chore:` | Config, dependencies, housekeeping |
| `refactor:` | Code reorganized, no behavior change |

**Examples:**
```
feat: add channel profile specific mode
fix: Dispatcharr token not refreshing after 5 minutes
docs: update user guide with config.json examples
chore: bump ffmpeg base image to alpine 3.20
```

### What happens after you push to main

The `beta.yml` workflow runs automatically and:
1. Builds all 4 Docker images (api, web, renderer, ffmpeg)
2. Tags them as `:beta` and `:beta-abc1234` (short commit hash)
3. Pushes to `ghcr.io/YOURUSERNAME/scorestreamarr-pro-*:beta`

Your beta users can then pull the latest:
```bash
docker compose pull && docker compose up -d
```

---

## 4. Beta vs. Stable — How Versioning Works

ScoreStreamArr uses **Semantic Versioning**: `MAJOR.MINOR.PATCH`

| Number | Change when... |
|--------|---------------|
| MAJOR | Breaking changes — users need to reconfigure |
| MINOR | New features added (backward compatible) |
| PATCH | Bug fixes only |

**With `-beta` suffix:** Pre-release, may have rough edges, not yet recommended for everyone.  
**Without suffix:** Stable, tested, recommended for all users.

### Version examples

| Version | Meaning |
|---------|---------|
| `v0.1.0-beta` | First public beta |
| `v0.2.0-beta` | New features in beta (channel profiles, numbering) |
| `v0.2.1-beta` | Bug fix on top of 0.2.0-beta |
| `v1.0.0` | First stable release |
| `v1.1.0` | New stable feature release |
| `v1.1.1` | Stable bug fix |

### Two image tracks

| Track | Tag | Built when | Who uses it |
|-------|-----|------------|-------------|
| Beta | `:beta` | Every push to main | Early adopters, testers |
| Stable | `:latest` | When you publish a version tag | Everyone else |
| Pinned | `:v0.2.0-beta` | Same as above | Users who don't want auto-updates |

---

## 5. Publishing a Release

When you're happy with what's in beta and want to promote it to stable (or tag a beta release so users can reference a specific version), you publish a **tag**.

### Publish a beta release

```bash
# Make sure all your changes are committed and pushed first
git status   # Should say "nothing to commit"

# Create the tag
git tag v0.2.0-beta

# Push the tag to GitHub (this triggers the release workflow)
git push origin v0.2.0-beta
```

### Promote to stable

When your beta is solid and you're ready to call it stable:

```bash
git tag v1.0.0
git push origin v1.0.0
```

This triggers `release.yml` which:
- Detects no `-beta` suffix → marks it as stable
- Builds and pushes `:latest`, `:v1.0.0`, and `:stable` tags to ghcr.io
- Creates a GitHub Release page automatically with install instructions

### Watch the build

1. Go to your repo on GitHub
2. Click **Actions** tab
3. You'll see the workflow running — click it to see live logs

Build takes about 5-10 minutes for multi-arch (amd64 + arm64).

### After publishing a release

1. Go to **Releases** in your repo sidebar
2. Click the release that was auto-created
3. Edit it to add your handwritten release notes
4. Copy content from the matching `docs/RELEASE-NOTES/v0.2.0-beta.md` file

---

## 6. Managing the Docker Images on ghcr.io

### Where users find your images

After your first successful build, images appear at:
```
https://github.com/YOURUSERNAME?tab=packages
```

Each image (api, web, renderer, ffmpeg) is listed separately.

### Making packages public

By default new packages are private. To make them public so users can pull without logging in:

1. Go to `https://github.com/YOURUSERNAME/scorestreamarr-pro-api/settings`  
   (repeat for each of the 4 packages: api, web, renderer, ffmpeg)
2. Scroll to "Danger Zone" → **Change package visibility** → Public

### What users type to pull your image

```bash
# In their .env file:
GITHUB_OWNER=YOURUSERNAME
SCORESTREAMARR_TAG=latest

# Or to pull manually:
docker pull ghcr.io/YOURUSERNAME/scorestreamarr-pro-api:latest
```

### Deleting old images

Packages accumulate over time. To clean up old beta SHA tags:
1. Go to **Packages** on your profile
2. Open a package → **Package settings** → **Manage versions**
3. Select old versions → Delete

---

## 7. Writing Release Notes

Release notes go in two places:
- `docs/RELEASE-NOTES/v0.X.X.md` — the source file in your repo
- GitHub Releases page — copy/paste from the file above

### Template

Create `docs/RELEASE-NOTES/v0.2.0-beta.md`:

```markdown
# ScoreStreamArr v0.2.0-beta — Release Notes

**Released:** 2026-02-19  
**Track:** Beta  
**Docker tag:** `ghcr.io/YOURUSERNAME/scorestreamarr-pro-api:v0.2.0-beta`

---

## What's New

### Channel Profile Assignment
You can now control which Dispatcharr Channel Profiles ScoreStreamArr channels
appear in. Three modes are available in `config.json`:
- **All** (default) — channels appear in every profile
- **None** — no automatic profile assignment
- **Specific** — choose exact profile IDs

### Flexible Channel Numbering
Channel numbers are now fully configurable via `config.json`. Choose between
auto mode (sequential from a base number) or manual mode (set each channel
individually). Individual channels can also be disabled.

---

## Changes from v0.1.0-beta

- Added `config/config.json` for persistent channel configuration
- Added `channel_profile_ids` support on channel creation and updates
- Added per-channel `enabled` flag
- Added per-channel name override
- Improved API logging — profile IDs now printed at startup
- Docker images now published to ghcr.io with multi-arch support (amd64 + arm64)
- GitHub Actions CI/CD workflows added

---

## Upgrade Instructions

### From v0.1.0-beta

1. Pull the new images:
   ```bash
   SCORESTREAMARR_TAG=v0.2.0-beta docker compose pull
   ```

2. Add the config volume to your `docker-compose.yml` if upgrading manually:
   ```yaml
   scorestreamarr-api:
     volumes:
       - ./config:/config
   ```

3. Create `config/config.json` from the template in the repo

4. Restart:
   ```bash
   docker compose up -d
   ```

---

## Known Issues

- Per-sport video streams all use the same full-scoreboard source (single renderer)
  — true isolated sport streams planned for v0.3.0

---

## Checksums (for verification)

```
sha256: [auto-generated by GitHub Actions]
```
```

---

## 8. Maintaining the User Guide

The User Guide (`docs/USER-GUIDE.md`) is the document for people who download ScoreStreamArr and need to set it up. Keep it updated whenever you add a feature.

### Sections to maintain

| Section | Update when... |
|---------|---------------|
| Requirements | You change minimum specs or dependencies |
| Installation | docker-compose.yml changes |
| Configuration Reference | New config.json fields added |
| Channel Profiles | Profile logic changes |
| Environment Variables | New `.env` variables added |
| Troubleshooting | New common problems identified |
| FAQ | Users ask the same question twice |

### Rules for a good user guide

- **Write for someone who has never done this before**
- Every code block should be copy-pasteable and actually work
- When something can go wrong, explain how to recover
- Use the exact filenames and variable names from the code
- Link to the CHANGELOG for "what changed" — don't duplicate it in the guide

---

## 9. GitHub Wiki Setup

The GitHub Wiki is a great place for longer documentation that doesn't fit in files.

### Enable the Wiki

1. Repo **Settings** → **Features** → check **Wikis** → Save

### Recommended Wiki pages

| Page | Contents |
|------|---------|
| Home | Quick links to everything |
| Installation | Full install walkthrough |
| Configuration | config.json reference |
| Channel Profiles | Dispatcharr profile integration |
| Troubleshooting | Common problems and fixes |
| FAQ | Frequently asked questions |
| Roadmap | Planned features |
| Contributing | How to contribute code |

### Linking from README to Wiki

At the top of your README, add:
```markdown
📖 **[Full Documentation →](https://github.com/YOURUSERNAME/scorestreamarr-pro/wiki)**
```

### Keeping Wiki and docs/ in sync

The `docs/` folder in the repo is version-controlled (tied to code versions).  
The Wiki is separate — better for content that applies across all versions.

Rule of thumb:
- **docs/ in repo** → installation steps tied to a specific version
- **Wiki** → conceptual guides, troubleshooting, FAQ that applies generally

---

## 10. Troubleshooting Common Git Problems

### "I pushed code and nothing built"

Check the Actions tab. Common causes:
- Workflow file has a YAML syntax error → red ❌ next to the workflow
- The push was to a branch other than `main`
- GitHub Actions was disabled on the repo

### "I made a mistake in my last commit"

If you haven't pushed yet:
```bash
git commit --amend -m "corrected commit message"
```

If you already pushed, the safest fix is a new commit:
```bash
# Make the fix, then:
git add .
git commit -m "fix: correct the thing I messed up"
git push
```

### "I pushed the wrong file (like my .env with passwords)"

1. Immediately invalidate any exposed credentials (change passwords, revoke tokens)
2. Remove from git history:
   ```bash
   git rm --cached .env
   echo ".env" >> .gitignore
   git commit -m "chore: remove .env from tracking"
   git push
   ```
3. The file is still in git history — if it had real credentials, contact GitHub support to purge history.

### "I want to undo changes to a file"

```bash
# Discard uncommitted changes to one file
git checkout -- scorestreamarr/api/app.py

# Discard ALL uncommitted changes (careful — this is permanent)
git checkout -- .
```

### "My build failed with 'permission denied' on ghcr.io"

The workflow token needs write permission:
1. Repo **Settings** → **Actions** → **General**
2. **Workflow permissions** → **Read and write permissions** → Save

### "I deleted a tag by mistake and the release disappeared"

```bash
# Re-create the tag at the current commit
git tag v0.2.0-beta
git push origin v0.2.0-beta
```

Then manually re-create the release on GitHub → Releases → Draft a new release.

### "How do I see all my tags?"

```bash
git tag           # list all tags
git tag -l "v0.*" # list tags matching a pattern
```

---

## Quick Reference Card

```bash
# ── Daily workflow ──────────────────────────────────────
git status                          # See what changed
git add .                           # Stage everything
git commit -m "feat: description"   # Save snapshot
git push                            # Upload to GitHub

# ── Releases ────────────────────────────────────────────
git tag v0.2.0-beta                 # Create a tag
git push origin v0.2.0-beta         # Publish the tag (triggers build)

# ── View history ────────────────────────────────────────
git log --oneline -10               # Last 10 commits, compact
git tag                             # All tags

# ── Fix mistakes ────────────────────────────────────────
git checkout -- filename            # Undo changes to a file
git commit --amend -m "new msg"     # Fix last commit message (before push only)
```
