# Updated wiki pages

These are the wiki pages brought up to date with 3.1.4 and `main` (new editor guide, towns, pip
install, physics, changelog, corrected wording everywhere the old interface was described).
They could not be pushed to the wiki from the session that wrote them, so they wait here.

To publish them:

```bash
git clone https://github.com/lolgg313/Heaven3D.wiki.git
cp Heaven3D/docs/wiki/*.md Heaven3D.wiki/        # every file except this README.md
rm Heaven3D.wiki/README.md
cd Heaven3D.wiki && git add -A && git commit -m "Wiki up to date with 3.1.4 and main" && git push
```

Pages not in this folder did not need a change.
