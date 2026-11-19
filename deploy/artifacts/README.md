# Space artifacts

The export script fills this folder only inside the generated Space folder with
the nine allowlisted local data/model files and `MANIFEST.sha256`. These generated
files stay git-ignored in the main repository. This placeholder keeps Docker's
COPY source available when no artifacts are shipped; the build then regenerates
the data and models.
