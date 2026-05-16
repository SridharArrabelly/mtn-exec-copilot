# Custom photo avatar — source asset

This folder holds the **source photo** used to train the custom photo avatar
that the app talks to via Voice Live.

The trained avatar itself lives in the Azure Speech / AI Services resource
pointed to by `AZURE_VOICELIVE_ENDPOINT` — this image is kept here only for
reproducibility (re-training, swapping characters, audit trail).

## Fill in when you commit a photo

- **File:** `Nuru.jpg`
- **Trained character name:** `Nuru`
- **Speech / AI Services resource:** `eastus2`
- **Trained on:** `2026-05-15`
- **Source / consent:** `Generated image, not a real person`

## Notes

- Not consumed at runtime by the backend or frontend.
- Not ingested by `scripts/setup_aisearch_index.py` (image extensions are ignored).
- Keep files small (downscale to a few MB) or use Git LFS for large originals:
  `git lfs track "assets/avatar/*.jpg"`.
- If the photo shows a real person without public-repo consent, do NOT commit it —
  add `assets/avatar/*.jpg` to `.gitignore` and keep it local.