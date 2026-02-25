#!/bin/bash
# Deploy dev.html -> index.html with automatic backup tag
set -e
cd "$(dirname "$0")"

# Get current version
LAST_TAG=$(git tag --sort=-v:refname | head -1)
echo "Última versão: $LAST_TAG"

# Auto-increment
if [[ $LAST_TAG =~ ^v([0-9]+)\.([0-9]+)$ ]]; then
  MAJOR="${BASH_REMATCH[1]}"
  MINOR="${BASH_REMATCH[2]}"
  NEW_VERSION="v${MAJOR}.$((MINOR+1))"
else
  NEW_VERSION="v1.1"
fi

echo "Nova versão: $NEW_VERSION"
read -p "Confirma deploy? (y/n) " -n 1 -r
echo
if [[ ! $REPLY =~ ^[Yy]$ ]]; then
  echo "Cancelado."
  exit 1
fi

# Tag current state as backup
git tag -a "${NEW_VERSION}-pre" -m "Backup antes do deploy $NEW_VERSION"

# Copy dev to prod (swap localStorage key from orgchart-dev to orgchart)
sed 's/orgchart-dev/orgchart/g; s/orgchart-backups/orgchart-prod-backups/g' dev.html > index.html
# Remove dev banner and adjust toolbar top
sed -i '' 's|<div class="dev-banner">.*</div>||' index.html
sed -i '' 's|top:32px|top:0|' index.html
sed -i '' 's|padding:112px|padding:80px|' index.html

git add -A
git commit -m "deploy: $NEW_VERSION"
git tag -a "$NEW_VERSION" -m "Release $NEW_VERSION"
git push && git push --tags

echo "✅ Deploy $NEW_VERSION concluído!"
echo "Tags criadas: ${NEW_VERSION}-pre (backup) e ${NEW_VERSION} (release)"
