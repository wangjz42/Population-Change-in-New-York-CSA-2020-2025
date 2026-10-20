// Build the site for GitHub Pages and publish it to the gh-pages branch.
// Usage: npm run deploy   (needs a GitHub remote named "origin")
import { execSync } from 'node:child_process';
import { writeFileSync } from 'node:fs';
import ghpages from 'gh-pages';

const remote = execSync('git remote get-url origin', { encoding: 'utf8' }).trim();
const repo = remote.replace(/\.git$/, '').split(/[/:]/).pop();
// <user>.github.io repos are served from the root; other repos from /<repo>
const base = repo.endsWith('.github.io') ? '' : `/${repo}`;

console.log(`Building with base path "${base || '/'}" ...`);
execSync('npx vite build', { stdio: 'inherit', env: { ...process.env, BASE_PATH: base } });
writeFileSync('build/.nojekyll', ''); // keep GitHub from hiding the _app folder

console.log('Publishing build/ to the gh-pages branch ...');
ghpages.publish('build', { dotfiles: true, message: 'Deploy site' }, (err) => {
	if (err) {
		console.error(err);
		process.exit(1);
	}
	const user = remote.match(/github\.com[/:]([^/]+)\//)?.[1] ?? '<user>';
	console.log(`Done. Site: https://${user.toLowerCase()}.github.io${base}/`);
});
