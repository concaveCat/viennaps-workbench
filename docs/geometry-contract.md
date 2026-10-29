# P0 geometry contract

This contract is based on bounded calls to the installed ViennaPS 4.6.2 package, not the newer source tree.

## Coordinates and domain

- User coordinates are `x` horizontal and `h` vertical. In 2D `Mesh.getNodes()` returns `[x, h, 0]`, so the adapter uses the fixed map `x=node[0]`, `h=node[1]`, `z=node[2]`. It never guesses an axis from spans.
- `h=0` is the substrate top datum. A positive trench depth extends toward negative h. Positive isotropic deposition extends toward positive h; negative rate etches toward negative h.
- The supported Domain construction is `Domain(gridDelta, xExtent, boundaryType)`. For tested reflective extents 4 and 5, x bounds were centered at `[-xExtent/2, +xExtent/2]`. Periodic boundaries read back as periodic on x and infinite on h; periodic mesh coordinates may include a seam point at `+xExtent/2` while the Domain bounding box ends at `+xExtent/2-gridDelta`.
- Passing a `yExtent` to the four-argument constructor did not change the tested trench bounds. The application does not expose a vertical computational extent. The manifest records the requested x extent, read-back bounding box and enum values.

## Structures and material identity

- Plane: `MakePlane(domain, height=0.0, material=Material.Si, addToExisting=False)`.
- Trench: the named constructor arguments are `trenchWidth`, `trenchDepth`, `trenchTaperAngle`, `maskHeight`, `maskTaperAngle`, `halfTrench=False`, `material=Si`, and `maskMaterial=Mask`.
- In tested samples, positive trench taper narrowed the bottom and negative taper widened it. Width and wall positions are grid-discretized; the app reports actual mesh coordinates and does not relabel those values as exact continuous geometry.
- A positive mask height creates material ID 0 (`Mask`); silicon is ID 10 and silicon dioxide is ID 30. The surface mesh stores `MaterialIds` as cell data aligned with line cells. The current Domain material map maps snapshot-local level-set indexes to IDs; it is not a stable layer ID.
- `addInterfaces=True`, `sharpCorners=False`, and `minNodeDistanceFactor=0.01` are fixed for both viewer and VTK export. `addInterfaces=False` and `sharpCorners=True` are unsafe on this install and are not called.

## Recipe geometry steps

- `removeMaterial`, `planarize` and `boolean` steps call the native operations on the previous step's `Domain`.
- `Planarize(domain, cutoffHeight)` accepts a height only. A `planarize` step with `stopMaterial` derives that height from the measured top of the stop material; it is a global height cut, not a CMP selectivity model. See `cmp-planarize-contract.md`.
- Every executed step records the measured change of each material's exposed top (`measuredRemoval`/`measuredAdded`) from the surface mesh it already extracts. No volume mesh is built for the measurement.

## Discretization and geometry validation

- The adapter stores requested and actual grid spacing and extents separately. It rejects non-finite inputs and a grid extent that cannot be represented within the accepted grid-interval budget.
- Positive trench opening width, depth, and enabled mask thickness must each span at least two grid intervals. Values between two and five intervals receive a precision warning. Zero mask thickness means no mask; zero taper means a vertical wall.
- For taper angle θ and depth d, the expected continuous bottom width is `width - 2*d*tan(θ)` under the observed sign convention. It must remain positive and span at least two grid intervals. The raster mesh is authoritative for the displayed geometry.
- After geometry creation, the adapter checks finite coordinates, valid line indexes, a `MaterialIds` value per line, and both positive and negative trench sidewall segments for the Si material. It rejects an incomplete contour with a field-specific explanation rather than presenting an incomplete surface as a successful preview.
- The specific `gridDelta=0.1`, `xExtent=4`, `width=1.2`, `depth=1`, `angle=0` probe omitted the positive-x sidewall. The sidewall check catches this case. It is not corrected by fabricating or mirroring vertices.
- No finite preview envelope is drawn as a process boundary. The 2D view uses returned interface cells only; axis extent and view padding are presentation settings.

