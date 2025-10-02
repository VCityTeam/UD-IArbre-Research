import numpy as np
import jax
import jax.numpy as jnp
from shapely.geometry import LineString
from rasterio import transform


def compute_flow_direction(dem):
    """Compute flow direction using only 8 directions
    Args:
        - dem (np.array): Raster DEM
    """
    rows, cols = dem.shape
    flow_dir = np.zeros((rows, cols), dtype=int)

    # 8 directions: 1=E, 2=SE, 4=S, 8=SW, 16=W, 32=NW, 64=N, 128=NE
    directions = [
        (0, 1, 1),
        (1, 1, 2),
        (1, 0, 4),
        (1, -1, 8),
        (0, -1, 16),
        (-1, -1, 32),
        (-1, 0, 64),
        (-1, 1, 128),
    ]

    for i in range(1, rows - 1):
        for j in range(1, cols - 1):
            if np.isnan(dem[i, j]):
                continue

            max_slope = 0
            steepest_dir = 0

            for di, dj, dir_code in directions:
                ni, nj = i + di, j + dj
                if 0 <= ni < rows and 0 <= nj < cols and not np.isnan(dem[ni, nj]):
                    slope_val = (dem[i, j] - dem[ni, nj]) / np.sqrt(di * di + dj * dj)
                    if slope_val > max_slope:
                        max_slope = slope_val
                        steepest_dir = dir_code

            flow_dir[i, j] = steepest_dir

    return flow_dir


@jax.jit
def compute_flow_direction_jax(dem):
    dem_jax = jnp.array(dem)
    rows, cols = dem_jax.shape

    directions = jnp.array(
        [
            [0, 1, 1, 1.0],
            [1, 1, 2, 1.414],
            [1, 0, 4, 1.0],
            [1, -1, 8, 1.414],
            [0, -1, 16, 1.0],
            [-1, -1, 32, 1.414],
            [-1, 0, 64, 1.0],
            [-1, 1, 128, 1.414],
        ]
    )

    padded_dem = jnp.pad(dem_jax, ((1, 1), (1, 1)), constant_values=jnp.nan)

    # Pre-define the shifts using concrete indices
    shifts = [
        padded_dem[1: rows + 1, 2: cols + 2],  # [0, 1]
        padded_dem[2: rows + 2, 2: cols + 2],  # [1, 1]
        padded_dem[2: rows + 2, 1: cols + 1],  # [1, 0]
        padded_dem[2: rows + 2, 0:cols],  # [1, -1]
        padded_dem[1: rows + 1, 0:cols],  # [0, -1]
        padded_dem[0:rows, 0:cols],  # [-1, -1]
        padded_dem[0:rows, 1: cols + 1],  # [-1, 0]
        padded_dem[0:rows, 2: cols + 2],  # [-1, 1]
    ]

    shifted_dems = jnp.stack(shifts, axis=0)
    dem_broadcast = jnp.broadcast_to(dem_jax[None, :, :], (8, rows, cols))

    elevation_diffs = dem_broadcast - shifted_dems
    distances = directions[:, 3].reshape(-1, 1, 1)
    slopes = elevation_diffs / distances

    valid_mask = (~jnp.isnan(shifted_dems)) & (slopes > 0)
    slopes = jnp.where(valid_mask, slopes, -jnp.inf)

    steepest_indices = jnp.argmax(slopes, axis=0)
    direction_codes = directions[:, 2].astype(int)
    flow_dir = direction_codes[steepest_indices]

    has_flow = jnp.max(slopes, axis=0) > -jnp.inf
    flow_dir = jnp.where(has_flow, flow_dir, 0)

    valid_dem = ~jnp.isnan(dem_jax)
    flow_dir = jnp.where(valid_dem, flow_dir, 0)

    return flow_dir


def compute_flow_accumulation(flow_dir):
    """Compute flow accumulation - number of cells that flow into each cell"""
    rows, cols = flow_dir.shape
    flow_acc = np.ones_like(flow_dir, dtype=np.float32)

    # Direction mappings for the 8 directions
    dir_map = {
        1: (0, 1),
        2: (1, 1),
        4: (1, 0),
        8: (1, -1),
        16: (0, -1),
        32: (-1, -1),
        64: (-1, 0),
        128: (-1, 1),
    }

    # Multiple passes to ensure all flow is accumulated
    for iteration in range(max(rows, cols)):
        changed = False
        for i in range(rows):
            for j in range(cols):
                if flow_dir[i, j] == 0 or np.isnan(flow_dir[i, j]):
                    continue

                # Find where this cell flows to
                if flow_dir[i, j] in dir_map:
                    di, dj = dir_map[flow_dir[i, j]]
                    ni, nj = i + di, j + dj

                    # Check if target cell is valid
                    if (
                        0 <= ni < rows
                        and 0 <= nj < cols
                        and not np.isnan(flow_dir[ni, nj])
                    ):
                        # Add this cell's accumulation to the downstream cell
                        old_acc = flow_acc[ni, nj]
                        flow_acc[ni, nj] += flow_acc[i, j]
                        if flow_acc[ni, nj] != old_acc:
                            changed = True

        if not changed:
            break

    return flow_acc


@jax.jit
def compute_flow_accumulation_jax(flow_dir, max_iterations=None):
    flow_dir_jax = jnp.array(flow_dir)
    rows, cols = flow_dir_jax.shape

    if max_iterations is None:
        max_iterations = max(rows, cols)

    flow_acc = jnp.ones_like(flow_dir_jax, dtype=jnp.float32)

    dir_offsets = jnp.array(
        [[0, 1], [1, 1], [1, 0], [1, -1], [0, -1], [-1, -1], [-1, 0], [-1, 1]]
    )
    dir_codes = jnp.array([1, 2, 4, 8, 16, 32, 64, 128])

    def single_iteration(flow_acc):

        i_coords = jnp.arange(rows)[:, None]
        j_coords = jnp.arange(cols)[None, :]

        for k in range(8):
            mask = flow_dir_jax == dir_codes[k]
            di, dj = dir_offsets[k]

            target_i = i_coords + di
            target_j = j_coords + dj

            valid_targets = (
                (target_i >= 0)
                & (target_i < rows)
                & (target_j >= 0)
                & (target_j < cols)
                & (~jnp.isnan(flow_dir_jax[target_i, target_j]))
                & mask
            )

            updates = jnp.where(valid_targets, flow_acc, 0.0)
            flow_acc = flow_acc.at[target_i, target_j].add(updates, mode="drop")

        return flow_acc

    def body_fun(carry):
        iteration, flow_acc = carry
        new_flow_acc = single_iteration(flow_acc)
        return iteration + 1, new_flow_acc

    def cond_fun(carry):
        iteration, flow_acc = carry
        prev_flow_acc = single_iteration(flow_acc)
        changed = jnp.any(prev_flow_acc != flow_acc)
        under_max = iteration < max_iterations
        first_or_changed = (iteration == 0) | changed
        return under_max & first_or_changed

    _, final_flow_acc = jax.lax.while_loop(cond_fun, body_fun, (0, flow_acc))

    return final_flow_acc


def compute_accumulation_threshold(flow_acc, min_accumulation=None):
    if min_accumulation is not None:
        return min_accumulation

    valid_acc = flow_acc[~np.isnan(flow_acc) & (flow_acc > 1)]
    if len(valid_acc) > 0:
        return np.percentile(valid_acc, 50)
    else:
        return 10


def get_flow_direction_map():
    return {
        1: (0, 1),
        2: (1, 1),
        4: (1, 0),
        8: (1, -1),
        16: (0, -1),
        32: (-1, -1),
        64: (-1, 0),
        128: (-1, 1),
    }


def trace_single_stream(
    start_i, start_j, stream_mask, flow_dir, dem_transform, visited
):
    rows, cols = stream_mask.shape
    dir_map = get_flow_direction_map()
    line_coords = []
    current_i, current_j = start_i, start_j

    while (
        0 <= current_i < rows
        and 0 <= current_j < cols
        and stream_mask[current_i, current_j]
        and not visited[current_i, current_j]
    ):
        visited[current_i, current_j] = True
        x, y = transform.xy(dem_transform, current_i, current_j)
        line_coords.append((x, y))

        flow_val = flow_dir[current_i, current_j]
        if flow_val in dir_map:
            di, dj = dir_map[flow_val]
            current_i += di
            current_j += dj
        else:
            break

    return line_coords


def trace_all_streams(stream_mask, flow_dir, dem_transform):
    rows, cols = stream_mask.shape
    stream_network = []
    visited = np.zeros_like(stream_mask, dtype=bool)

    for i in range(rows):
        for j in range(cols):
            if stream_mask[i, j] and not visited[i, j]:
                line_coords = trace_single_stream(
                    i, j, stream_mask, flow_dir, dem_transform, visited
                )

                if len(line_coords) >= 2:
                    try:
                        line = LineString(line_coords)
                        stream_network.append(line)
                    except Exception as e:
                        print(e)
                        pass

    return stream_network


def extract_stream_network(flow_acc, dem_transform, flow_dir, min_accumulation=None):
    threshold = compute_accumulation_threshold(flow_acc, min_accumulation)
    print(f"Using flow accumulation threshold: {threshold}")

    stream_mask = (flow_acc >= threshold) & (~np.isnan(flow_acc))
    stream_network = trace_all_streams(stream_mask, flow_dir, dem_transform)

    return stream_network, stream_mask, threshold


def process_hydrology_jax(dem, dem_transform):
    print("Computing flow directions...")
    flow_direction = compute_flow_direction_jax(dem)

    print("Computing flow accumulation...")
    flow_accumulation_jax = compute_flow_accumulation_jax(flow_direction)

    # Convert JAX arrays to NumPy for downstream processing
    print("Converting to Numpy format")
    flow_direction = np.array(flow_direction)
    flow_accumulation = np.array(flow_accumulation_jax)

    print("Extracting stream network...")
    stream_network, stream_mask, threshold = extract_stream_network(
        flow_accumulation, dem_transform, flow_direction
    )

    return flow_direction, flow_accumulation, stream_network, stream_mask, threshold
