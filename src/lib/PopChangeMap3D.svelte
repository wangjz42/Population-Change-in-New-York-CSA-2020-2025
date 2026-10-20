<script>
	import { onMount } from 'svelte';
	import { asset } from '$app/paths';

	let {
		csvUrl = asset('county_pop_estimates_csa408_wide.csv'),
		geoUrl = asset('nycsa_counties.geojson')
	} = $props();

	// ---------- tunables ----------
	const MAP_SIZE = 100; // world units across the longest side of the CSA
	const UNITS_PER_PCT = 9; // bar height per 1% change
	const BAR_W = 1.5;
	const ANIM_MS = 700;
	const PLAY_MS = 1600;

	// ---------- reactive UI state ----------
	let container;
	let canvasHost;
	let labelLayer;
	let years = $state([]);
	let yearIdx = $state(0);
	let playing = $state(false);
	let hovered = $state(null); // geoid
	let pointer = $state({ x: 0, y: 0 });
	let counties = $state([]);
	let total = $state(null);
	let status = $state('loading');
	let errorMsg = $state('');

	const year = $derived(years[yearIdx]);
	const nf = new Intl.NumberFormat('en-US');
	const fmtPct = (v) => (v > 0 ? '+' : v < 0 ? '−' : '') + Math.abs(v).toFixed(2) + '%';
	const fmtInt = (v) => (v > 0 ? '+' : v < 0 ? '−' : '') + nf.format(Math.abs(Math.round(v)));
	const pctOf = (row, y) => (100 * (row.pop[y] - row.pop[y - 1])) / row.pop[y - 1];

	const hoveredRow = $derived(counties.find((c) => c.geoid === hovered) ?? null);
	const totalPct = $derived(total && year ? pctOf(total, year) : null);

	// ---------- non-reactive three.js handles ----------
	let setYearTargets = () => {};
	let resetView = () => {};

	function parseCSV(text) {
		const rows = [];
		for (const line of text.trim().split(/\r?\n/)) {
			const out = [];
			let cur = '',
				q = false;
			for (let i = 0; i < line.length; i++) {
				const ch = line[i];
				if (q) {
					if (ch === '"' && line[i + 1] === '"') (cur += '"'), i++;
					else if (ch === '"') q = false;
					else cur += ch;
				} else if (ch === '"') q = true;
				else if (ch === ',') out.push(cur), (cur = '');
				else cur += ch;
			}
			out.push(cur);
			rows.push(out);
		}
		const [head, ...body] = rows;
		return body.map((r) => Object.fromEntries(head.map((h, i) => [h, r[i]])));
	}

	const shortName = (n) => n.replace(/ County$/, '').replace(/ Planning Region$/, '');

	// ---------- geometry helpers ----------
	function pointInRing(x, y, ring) {
		let inside = false;
		for (let i = 0, j = ring.length - 1; i < ring.length; j = i++) {
			const [xi, yi] = ring[i],
				[xj, yj] = ring[j];
			if (yi > y !== yj > y && x < ((xj - xi) * (y - yi)) / (yj - yi) + xi) inside = !inside;
		}
		return inside;
	}
	function segDist(px, py, [ax, ay], [bx, by]) {
		const dx = bx - ax,
			dy = by - ay,
			l = dx * dx + dy * dy;
		let t = l ? ((px - ax) * dx + (py - ay) * dy) / l : 0;
		t = Math.max(0, Math.min(1, t));
		return Math.hypot(px - ax - t * dx, py - ay - t * dy);
	}
	function ringArea(r) {
		let a = 0;
		for (let i = 0, j = r.length - 1; i < r.length; j = i++) a += (r[j][0] + r[i][0]) * (r[j][1] - r[i][1]);
		return Math.abs(a / 2);
	}
	// interior point farthest from the edges of the largest polygon (a cheap "pole of inaccessibility")
	function anchorPoint(polys) {
		const poly = polys.reduce((a, b) => (ringArea(b[0]) > ringArea(a[0]) ? b : a));
		const xs = poly[0].map((p) => p[0]),
			ys = poly[0].map((p) => p[1]);
		const [x0, x1, y0, y1] = [Math.min(...xs), Math.max(...xs), Math.min(...ys), Math.max(...ys)];
		let best = null,
			bestD = -1;
		const N = 40;
		for (let i = 0; i <= N; i++)
			for (let j = 0; j <= N; j++) {
				const x = x0 + ((x1 - x0) * i) / N,
					y = y0 + ((y1 - y0) * j) / N;
				if (!pointInRing(x, y, poly[0]) || poly.slice(1).some((h) => pointInRing(x, y, h))) continue;
				let d = Infinity;
				for (const ring of poly)
					for (let k = 0, m = ring.length - 1; k < ring.length; m = k++) d = Math.min(d, segDist(x, y, ring[m], ring[k]));
				if (d > bestD) (bestD = d), (best = [x, y]);
			}
		return best ?? [(x0 + x1) / 2, (y0 + y1) / 2];
	}

	onMount(() => {
		let disposed = false;
		let cleanup = () => {};

		(async () => {
			try {
				const [THREE, { OrbitControls }, { LineSegments2 }, { LineSegmentsGeometry }, { LineMaterial }, csvText, geo] =
					await Promise.all([
						import('three'),
						import('three/examples/jsm/controls/OrbitControls.js'),
						import('three/examples/jsm/lines/LineSegments2.js'),
						import('three/examples/jsm/lines/LineSegmentsGeometry.js'),
						import('three/examples/jsm/lines/LineMaterial.js'),
						fetch(csvUrl).then((r) => {
							if (!r.ok) throw new Error(`Couldn't load ${csvUrl}`);
							return r.text();
						}),
						fetch(geoUrl).then((r) => {
							if (!r.ok) throw new Error(`Couldn't load ${geoUrl}`);
							return r.json();
						})
					]);
				if (disposed) return;

				// ---- data ----
				const rows = parseCSV(csvText);
				const yearCols = Object.keys(rows[0]).filter((k) => /^\d{4}$/.test(k)).map(Number).sort();
				const toRow = (r) => ({
					geoid: r.geoid,
					name: r.county,
					short: shortName(r.county),
					state: r.state,
					pop: Object.fromEntries(yearCols.map((y) => [y, +r[y]]))
				});
				const countyRows = rows.filter((r) => r.geography === 'county').map(toRow);
				const totalRow = rows.find((r) => r.geography === 'CSA total');
				total = totalRow ? toRow(totalRow) : null;
				years = yearCols.slice(1); // first year is the baseline
				counties = countyRows;

				// ---- projection (equirectangular, corrected for latitude) ----
				const polysOf = (g) => (g.type === 'Polygon' ? [g.coordinates] : g.coordinates);
				let lo = [Infinity, Infinity],
					hi = [-Infinity, -Infinity];
				for (const f of geo.features)
					for (const poly of polysOf(f.geometry))
						for (const [x, y] of poly[0]) {
							lo = [Math.min(lo[0], x), Math.min(lo[1], y)];
							hi = [Math.max(hi[0], x), Math.max(hi[1], y)];
						}
				const lon0 = (lo[0] + hi[0]) / 2,
					lat0 = (lo[1] + hi[1]) / 2;
				const kx = Math.cos((lat0 * Math.PI) / 180);
				const k = MAP_SIZE / Math.max((hi[0] - lo[0]) * kx, hi[1] - lo[1]);
				const proj = ([lon, lat]) => [(lon - lon0) * kx * k, (lat - lat0) * k]; // x east, y north

				// ---- three.js scene ----
				const renderer = new THREE.WebGLRenderer({ antialias: true, alpha: true });
				renderer.setPixelRatio(Math.min(window.devicePixelRatio, 2));
				canvasHost.appendChild(renderer.domElement);
				const scene = new THREE.Scene();
				const camera = new THREE.PerspectiveCamera(38, 1, 0.5, 2000);
				const HOME = new THREE.Vector3(0, 70, 78);
				camera.position.copy(HOME);
				const controls = new OrbitControls(camera, renderer.domElement);
				controls.enableDamping = true;
				controls.dampingFactor = 0.08;
				controls.minDistance = 4;
				controls.maxDistance = 400;
				controls.minPolarAngle = 0;
				controls.maxPolarAngle = Math.PI; // allow looking from underneath
				controls.screenSpacePanning = true;
				controls.target.set(0, 0, 0);
				resetView = () => {
					camera.position.copy(HOME);
					controls.target.set(0, 0, 0);
				};

				scene.add(new THREE.HemisphereLight(0xffffff, 0x8a8a8a, 1.6));
				const sun = new THREE.DirectionalLight(0xffffff, 1.6);
				sun.position.set(60, 120, 40);
				scene.add(sun);
				const sun2 = new THREE.DirectionalLight(0xffffff, 0.6);
				sun2.position.set(-60, -80, -40); // lights bars below the map
				scene.add(sun2);

				// theme
				const mq = window.matchMedia('(prefers-color-scheme: dark)');
				const palette = () => {
					const s = getComputedStyle(container);
					const v = (n) => s.getPropertyValue(n).trim();
					return {
						fill: v('--map-fill'),
						fillHover: v('--map-fill-hover'),
						border: v('--map-border'),
						outline: v('--map-outline'),
						up: v('--bar-up'),
						down: v('--bar-down')
					};
				};

				// ---- county fills (translucent so bars below the map stay visible) ----
				const fillMat = new THREE.MeshBasicMaterial({
					transparent: true,
					opacity: 0.55,
					side: THREE.DoubleSide,
					depthWrite: false
				});
				const fills = new Map();
				const anchors = new Map();
				const segCount = new Map();
				const segs = [];
				const key = (p) => p[0].toFixed(4) + ',' + p[1].toFixed(4);

				for (const f of geo.features) {
					const id = f.properties.geoid;
					const polys = polysOf(f.geometry).map((poly) => poly.map((ring) => ring.map(proj)));
					const shapes = polys.map((poly) => {
						const s = new THREE.Shape(poly[0].map(([x, y]) => new THREE.Vector2(x, y)));
						s.holes = poly.slice(1).map((h) => new THREE.Path(h.map(([x, y]) => new THREE.Vector2(x, y))));
						return s;
					});
					const mesh = new THREE.Mesh(new THREE.ShapeGeometry(shapes), fillMat.clone());
					mesh.rotation.x = -Math.PI / 2; // XY -> XZ, north = -Z
					mesh.userData.geoid = id;
					mesh.renderOrder = 0;
					scene.add(mesh);
					fills.set(id, mesh);
					anchors.set(id, anchorPoint(polys));

					// boundary segments; edges shared by two counties are interior borders
					for (const poly of polys)
						for (const ring of poly)
							for (let i = 1; i < ring.length; i++) {
								const a = ring[i - 1],
									b = ring[i];
								const kk = [key(a), key(b)].sort().join('|');
								if (!segCount.has(kk)) segs.push([kk, a, b]);
								segCount.set(kk, (segCount.get(kk) ?? 0) + 1);
							}
				}

				const inner = [],
					outer = [];
				for (const [kk, a, b] of segs) {
					const arr = segCount.get(kk) > 1 ? inner : outer;
					arr.push(a[0], 0.03, -a[1], b[0], 0.03, -b[1]);
				}
				const innerGeo = new THREE.BufferGeometry();
				innerGeo.setAttribute('position', new THREE.Float32BufferAttribute(inner, 3));
				const innerMat = new THREE.LineBasicMaterial({ transparent: true, opacity: 0.9 });
				const innerLines = new THREE.LineSegments(innerGeo, innerMat);
				innerLines.renderOrder = 1;
				scene.add(innerLines);

				const outerGeo = new LineSegmentsGeometry().setPositions(outer.map((v, i) => (i % 3 === 1 ? 0.05 : v)));
				const outerMat = new LineMaterial({ linewidth: 2.5, worldUnits: false });
				const outerLines = new LineSegments2(outerGeo, outerMat);
				outerLines.renderOrder = 2;
				scene.add(outerLines);

				// ---- bars ----
				const barGeo = new THREE.BoxGeometry(BAR_W, 1, BAR_W).translate(0, 0.5, 0);
				const bars = new Map();
				for (const c of countyRows) {
					const a = anchors.get(c.geoid);
					if (!a) continue;
					const mesh = new THREE.Mesh(barGeo, new THREE.MeshLambertMaterial());
					mesh.position.set(a[0], 0, -a[1]);
					mesh.scale.y = 0.001;
					mesh.userData = { geoid: c.geoid, from: 0, to: 0, h: 0 };
					mesh.renderOrder = 3;
					scene.add(mesh);
					bars.set(c.geoid, mesh);
				}

				let colors = palette();
				const applyTheme = () => {
					colors = palette();
					for (const [id, m] of fills) m.material.color.set(id === hovered ? colors.fillHover : colors.fill);
					innerMat.color.set(colors.border);
					outerMat.color.set(colors.outline);
					for (const m of bars.values()) m.material.color.set(m.userData.h >= 0 ? colors.up : colors.down);
				};
				applyTheme();
				mq.addEventListener('change', applyTheme);

				// ---- labels (HTML overlay, fade in when zoomed close) ----
				const labels = new Map();
				for (const c of countyRows) {
					const el = document.createElement('div');
					el.className = 'bar-label';
					labelLayer.appendChild(el);
					labels.set(c.geoid, el);
				}

				// ---- animation ----
				let animStart = 0;
				const ease = (t) => 1 - Math.pow(1 - t, 3);
				setYearTargets = (y, instant = false) => {
					for (const c of countyRows) {
						const m = bars.get(c.geoid);
						if (!m) continue;
						const pct = pctOf(c, y);
						m.userData.from = m.userData.h;
						m.userData.to = pct * UNITS_PER_PCT;
						m.userData.pct = pct;
						const el = labels.get(c.geoid);
						el.innerHTML = `<b>${c.short}</b> ${fmtPct(pct)}`;
						el.classList.toggle('down', pct < 0);
					}
					animStart = instant ? -Infinity : performance.now();
				};

				// ---- hover / raycast ----
				const ray = new THREE.Raycaster();
				const ndc = new THREE.Vector2();
				let dragging = false,
					downAt = null;
				const pickables = [...bars.values(), ...fills.values()];
				const setHover = (id) => {
					if (id === hovered) return;
					if (hovered) {
						fills.get(hovered)?.material.color.set(colors.fill);
						bars.get(hovered)?.material.emissive.setHex(0x000000);
					}
					hovered = id;
					if (id) {
						fills.get(id)?.material.color.set(colors.fillHover);
						bars.get(id)?.material.emissive.setHex(mq.matches ? 0x333333 : 0x222222);
					}
				};
				const pick = (e) => {
					const r = renderer.domElement.getBoundingClientRect();
					ndc.set(((e.clientX - r.left) / r.width) * 2 - 1, -((e.clientY - r.top) / r.height) * 2 + 1);
					ray.setFromCamera(ndc, camera);
					const hit = ray.intersectObjects(pickables, false)[0];
					pointer = { x: e.clientX - r.left, y: e.clientY - r.top };
					setHover(hit ? hit.object.userData.geoid : null);
				};
				const onMove = (e) => {
					if (downAt && Math.hypot(e.clientX - downAt[0], e.clientY - downAt[1]) > 4) dragging = true;
					if (!dragging) pick(e);
				};
				const onDown = (e) => {
					downAt = [e.clientX, e.clientY];
					dragging = false;
				};
				const onUp = (e) => {
					if (!dragging) pick(e); // tap to inspect on touch screens
					downAt = null;
					dragging = false;
				};
				const onLeave = () => {
					if (!downAt) setHover(null);
				};
				const el = renderer.domElement;
				el.addEventListener('pointermove', onMove);
				el.addEventListener('pointerdown', onDown);
				el.addEventListener('pointerup', onUp);
				el.addEventListener('pointerleave', onLeave);

				// ---- resize ----
				let W = 1,
					H = 1;
				const resize = () => {
					W = canvasHost.clientWidth;
					H = canvasHost.clientHeight;
					renderer.setSize(W, H, false);
					camera.aspect = W / H;
					camera.updateProjectionMatrix();
					outerMat.resolution.set(W, H);
				};
				const ro = new ResizeObserver(resize);
				ro.observe(canvasHost);
				resize();

				// ---- render loop ----
				const v = new THREE.Vector3();
				let raf;
				const tick = (now) => {
					raf = requestAnimationFrame(tick);
					const t = Math.min(1, (now - animStart) / ANIM_MS);
					const e = ease(t);
					for (const m of bars.values()) {
						const u = m.userData;
						u.h = u.from + (u.to - u.from) * e;
						m.scale.y = Math.max(Math.abs(u.h), 0.001);
						m.rotation.x = u.h < 0 ? Math.PI : 0; // flip downward below the map
						m.position.y = u.h < 0 ? -0.06 : 0.06; // keep bar caps off the map plane (no z-fighting)
						m.material.color.set(u.h >= 0 ? colors.up : colors.down);
					}
					controls.update();
					renderer.render(scene, camera);

					// labels: visible when the camera is close
					const dist = camera.position.distanceTo(controls.target);
					const alpha = Math.max(0, Math.min(1, (75 - dist) / 25));
					labelLayer.style.opacity = alpha;
					if (alpha > 0)
						for (const [id, lab] of labels) {
							const m = bars.get(id);
							v.set(m.position.x, m.userData.h + (m.userData.h >= 0 ? 0.8 : -0.8), m.position.z).project(camera);
							const vis = v.z < 1 && Math.abs(v.x) < 1.1 && Math.abs(v.y) < 1.1;
							lab.style.display = vis ? '' : 'none';
							if (vis) lab.style.transform = `translate(${((v.x + 1) / 2) * W}px, ${((1 - v.y) / 2) * H}px) translate(-50%, ${m.userData.h >= 0 ? '-100%' : '0'})`;
						}
				};
				raf = requestAnimationFrame(tick);

				status = 'ready';
				// start at the 2019 baseline (flat), then grow into the first year
				setTimeout(() => setYearTargets(years[yearIdx]), 250);

				cleanup = () => {
					cancelAnimationFrame(raf);
					ro.disconnect();
					mq.removeEventListener('change', applyTheme);
					controls.dispose();
					renderer.dispose();
					scene.traverse((o) => {
						o.geometry?.dispose();
						o.material?.dispose?.();
					});
					el.remove();
				};
			} catch (err) {
				console.error(err);
				errorMsg = err.message;
				status = 'error';
			}
		})();

		return () => {
			disposed = true;
			cleanup();
		};
	});

	// animate whenever the slider moves
	$effect(() => {
		if (status === 'ready' && year) setYearTargets(year);
	});

	// play / pause
	$effect(() => {
		if (!playing) return;
		const id = setInterval(() => {
			if (yearIdx >= years.length - 1) playing = false;
			else yearIdx += 1;
		}, PLAY_MS);
		return () => clearInterval(id);
	});
	function togglePlay() {
		if (!playing && yearIdx >= years.length - 1) yearIdx = 0;
		playing = !playing;
	}
</script>

<div class="viz" bind:this={container}>
	<header>
		<div>
			<h1>Population change by county, New York–Newark CSA</h1>
			<p class="sub">
				Annual % change in population, July 1 estimates. Bars rise above the map for growth and drop
				below it for decline.
			</p>
		</div>
		{#if total && year}
			<div class="stat">
				<span class="stat-label">CSA total, {year - 1} → {year}</span>
				<span class="stat-value">{fmtPct(totalPct)}</span>
				<span class="stat-note">{nf.format(total.pop[year - 1])} → {nf.format(total.pop[year])}</span>
			</div>
		{/if}
	</header>

	<div class="stage">
		<div class="canvas" bind:this={canvasHost}></div>
		<div class="labels" bind:this={labelLayer}></div>

		{#if status === 'loading'}
			<div class="msg">Loading map…</div>
		{:else if status === 'error'}
			<div class="msg">Couldn't build the map: {errorMsg}</div>
		{/if}

		<div class="legend">
			<span><i class="sw up"></i>Growth (above map)</span>
			<span><i class="sw down"></i>Decline (below map)</span>
		</div>
		<div class="hint">Drag to rotate · Right-drag or Shift-drag to pan · Scroll to zoom · Hover a bar for details</div>
		<button class="reset" onclick={() => resetView()}>Reset view</button>

		{#if hoveredRow && year}
			{@const p = pctOf(hoveredRow, year)}
			<div
				class="tip"
				style="left:{pointer.x}px; top:{pointer.y}px"
				class:flip={pointer.x > (canvasHost?.clientWidth ?? 0) - 240}
			>
				<div class="tip-title">{hoveredRow.name}</div>
				<div class="tip-sub">{hoveredRow.state}</div>
				<table>
					<tbody>
						<tr><td>{year - 1} population</td><td>{nf.format(hoveredRow.pop[year - 1])}</td></tr>
						<tr><td>{year} population</td><td>{nf.format(hoveredRow.pop[year])}</td></tr>
						<tr>
							<td>Change</td>
							<td>{fmtInt(hoveredRow.pop[year] - hoveredRow.pop[year - 1])}</td>
						</tr>
						<tr class="pct">
							<td><i class="sw" class:up={p >= 0} class:down={p < 0}></i>% change</td>
							<td>{fmtPct(p)}</td>
						</tr>
					</tbody>
				</table>
			</div>
		{/if}
	</div>

	{#if years.length}
		<div class="timeline">
			<button class="play" onclick={togglePlay} aria-label={playing ? 'Pause' : 'Play'}>
				{#if playing}
					<svg viewBox="0 0 16 16" width="14" height="14"><rect x="3" y="2" width="3.5" height="12" rx="1" /><rect x="9.5" y="2" width="3.5" height="12" rx="1" /></svg>
				{:else}
					<svg viewBox="0 0 16 16" width="14" height="14"><path d="M4 2.5v11a.5.5 0 0 0 .77.42l8.5-5.5a.5.5 0 0 0 0-.84l-8.5-5.5A.5.5 0 0 0 4 2.5z" /></svg>
				{/if}
			</button>
			<div class="slider">
				<input
					type="range"
					min="0"
					max={years.length - 1}
					step="1"
					bind:value={yearIdx}
					oninput={() => (playing = false)}
					aria-label="Year"
				/>
				<div class="ticks">
					{#each years as y, i}
						<button class:active={i === yearIdx} onclick={() => ((yearIdx = i), (playing = false))}>
							{y}
						</button>
					{/each}
				</div>
			</div>
		</div>
	{/if}
</div>

<style>
	.viz {
		--surface: #fcfcfb;
		--surface-raised: #ffffff;
		--text-primary: #0b0b0b;
		--text-secondary: #52514e;
		--text-muted: #7a7974;
		--rule: #e4e3df;
		--map-fill: #e9e8e4;
		--map-fill-hover: #d3d6dc;
		--map-border: #a8a7a1;
		--map-outline: #3a3a37;
		--bar-up: #2a78d6;
		--bar-down: #e34948;
		--accent: #2a78d6;

		position: relative;
		display: flex;
		flex-direction: column;
		height: 100vh;
		height: 100dvh;
		background: var(--surface);
		color: var(--text-primary);
		font-family: system-ui, -apple-system, 'Segoe UI', Roboto, sans-serif;
	}
	@media (prefers-color-scheme: dark) {
		.viz {
			--surface: #1a1a19;
			--surface-raised: #262624;
			--text-primary: #f0efec;
			--text-secondary: #c3c2b7;
			--text-muted: #8f8e86;
			--rule: #383835;
			--map-fill: #2e2e2b;
			--map-fill-hover: #44474d;
			--map-border: #5e5d58;
			--map-outline: #d6d5cf;
			--bar-up: #3987e5;
			--bar-down: #e66767;
			--accent: #3987e5;
		}
	}

	header {
		display: flex;
		justify-content: space-between;
		align-items: flex-start;
		gap: 16px;
		padding: 16px 20px 8px;
		flex-wrap: wrap;
	}
	h1 {
		font-size: 18px;
		font-weight: 600;
		margin: 0 0 4px;
	}
	.sub {
		margin: 0;
		font-size: 13px;
		color: var(--text-secondary);
		max-width: 60ch;
	}
	.stat {
		display: flex;
		flex-direction: column;
		align-items: flex-end;
		text-align: right;
	}
	.stat-label,
	.stat-note {
		font-size: 12px;
		color: var(--text-secondary);
	}
	.stat-value {
		font-size: 26px;
		font-weight: 600;
		font-variant-numeric: tabular-nums;
		line-height: 1.2;
	}

	.stage {
		position: relative;
		flex: 1;
		min-height: 0;
		overflow: hidden;
	}
	.canvas,
	.labels {
		position: absolute;
		inset: 0;
	}
	.canvas :global(canvas) {
		width: 100%;
		height: 100%;
		display: block;
		touch-action: none;
		cursor: grab;
	}
	.canvas :global(canvas:active) {
		cursor: grabbing;
	}
	.labels {
		pointer-events: none;
		transition: opacity 0.2s;
	}
	.labels :global(.bar-label) {
		position: absolute;
		left: 0;
		top: 0;
		font-size: 11px;
		white-space: nowrap;
		color: var(--text-primary);
		background: color-mix(in srgb, var(--surface) 80%, transparent);
		padding: 1px 5px;
		border-radius: 4px;
		font-variant-numeric: tabular-nums;
	}
	.labels :global(.bar-label b) {
		font-weight: 600;
	}

	.msg {
		position: absolute;
		inset: 0;
		display: grid;
		place-items: center;
		color: var(--text-secondary);
		font-size: 14px;
	}
	.legend {
		position: absolute;
		left: 20px;
		top: 8px;
		display: flex;
		gap: 14px;
		flex-wrap: wrap;
		font-size: 12px;
		color: var(--text-secondary);
		pointer-events: none;
	}
	.sw {
		display: inline-block;
		width: 10px;
		height: 10px;
		border-radius: 2px;
		margin-right: 6px;
		vertical-align: -1px;
	}
	.sw.up {
		background: var(--bar-up);
	}
	.sw.down {
		background: var(--bar-down);
	}
	.hint {
		position: absolute;
		left: 20px;
		bottom: 10px;
		font-size: 11px;
		color: var(--text-muted);
		pointer-events: none;
	}
	.reset {
		position: absolute;
		right: 16px;
		top: 8px;
		font: inherit;
		font-size: 12px;
		padding: 5px 10px;
		border-radius: 6px;
		border: 1px solid var(--rule);
		background: var(--surface-raised);
		color: var(--text-primary);
		cursor: pointer;
	}

	.tip {
		position: absolute;
		transform: translate(14px, -50%);
		min-width: 210px;
		padding: 10px 12px;
		background: var(--surface-raised);
		border: 1px solid var(--rule);
		border-radius: 8px;
		box-shadow: 0 4px 16px rgb(0 0 0 / 0.12);
		font-size: 12px;
		pointer-events: none;
	}
	.tip.flip {
		transform: translate(calc(-100% - 14px), -50%);
	}
	.tip-title {
		font-weight: 600;
		font-size: 13px;
	}
	.tip-sub {
		color: var(--text-secondary);
		margin-bottom: 6px;
	}
	.tip table {
		width: 100%;
		border-collapse: collapse;
	}
	.tip td {
		padding: 2px 0;
		color: var(--text-secondary);
	}
	.tip td:last-child {
		text-align: right;
		color: var(--text-primary);
		font-variant-numeric: tabular-nums;
		padding-left: 12px;
	}
	.tip .pct td {
		border-top: 1px solid var(--rule);
		padding-top: 5px;
		font-weight: 600;
		color: var(--text-primary);
	}

	.timeline {
		display: flex;
		align-items: flex-start;
		gap: 14px;
		padding: 10px 20px 16px;
		border-top: 1px solid var(--rule);
	}
	.play {
		flex: none;
		width: 34px;
		height: 34px;
		border-radius: 50%;
		border: 1px solid var(--rule);
		background: var(--surface-raised);
		color: var(--text-primary);
		display: grid;
		place-items: center;
		cursor: pointer;
		fill: currentColor;
		margin-top: 2px;
	}
	.slider {
		flex: 1;
		min-width: 0;
	}
	input[type='range'] {
		width: 100%;
		accent-color: var(--accent);
		margin: 8px 0 4px;
	}
	.ticks {
		display: flex;
		justify-content: space-between;
		padding: 0 8px;
	}
	.ticks button {
		font: inherit;
		font-size: 12px;
		background: none;
		border: none;
		color: var(--text-muted);
		cursor: pointer;
		padding: 2px 0;
		font-variant-numeric: tabular-nums;
		width: 48px;
		margin: 0 -24px;
		text-align: center;
	}
	.ticks button.active {
		color: var(--text-primary);
		font-weight: 600;
	}
	@media (max-width: 640px) {
		.hint {
			display: none;
		}
		header {
			padding: 12px 16px 6px;
		}
		.timeline {
			padding: 8px 16px 12px;
		}
	}
</style>
