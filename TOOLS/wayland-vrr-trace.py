#!/usr/bin/env python3
"""Summarize WAYLAND_DEBUG=client logs without changing client/compositor behavior.

Accepts both older [milliseconds] object@id and newer [HH:MM:SS.usec]
object#id traces. Counts per-surface commits, buffer/damage updates, callback-only
commits, callbacks and raw presentation feedback. Never infers actual VRR or
scanout from callback rate, nominal refresh, or absence of ZERO_COPY.
"""

import argparse
import collections
import json
import math
import pathlib
import re
import statistics

LINE = re.compile(r"\[([\d:.]+)\].*?(->\s*)?(\w+)[@#](\d+)\.(\w+)\((.*)\)\s*$")
OBJECT = re.compile(r"(\w+)[@#](\d+)")


def cadence(times):
    deltas = [b - a for a, b in zip(times, times[1:]) if b > a]
    if not deltas:
        return {"count": len(times), "intervals": 0}
    ordered = sorted(deltas)
    return {"count": len(times), "intervals": len(deltas),
            "min_ms": min(deltas), "median_ms": statistics.median(deltas),
            "p95_ms": ordered[math.ceil(len(ordered) * .95) - 1],
            "max_ms": max(deltas), "mean_hz": 1000 / statistics.mean(deltas)}


def surface_state():
    return {"commits": [], "updates": [], "callback_only": [], "empty": [],
            "damage_requests": 0, "attach_requests": 0,
            "pending_attach": False, "pending_damage": False,
            "pending_callbacks": [], "pending_feedback": [],
            "callback_requests": 0, "callback_done": [],
            "feedback_requests": 0, "feedback_latched": 0,
            "feedback_presented": 0, "feedback_discarded": 0,
            "feedback_cancelled": 0, "feedback_records": [],
            "opaque_regions": [], "buffer_scales": [], "transforms": []}


def analyze(lines):
    surfaces = collections.defaultdict(surface_state)
    callbacks = {}
    feedbacks = {}
    metadata = []
    matches = 0
    connection_starts = 0
    day_offset = 0
    previous_time = None
    for line in lines:
        match = LINE.search(line)
        if not match:
            continue
        stamp, arrow, interface, oid, op, args = match.groups()
        oid = int(oid)
        if ':' in stamp:
            h, m, s = stamp.split(':')
            now = (int(h) * 3600 + int(m) * 60 + float(s)) * 1000
            if previous_time is not None and now < previous_time - 12 * 3600000:
                day_offset += 86400000
            previous_time = now
            now += day_offset
        else:
            now = float(stamp)
        sent = bool(arrow)
        refs = [(kind, int(number)) for kind, number in OBJECT.findall(args)]
        matches += 1
        if sent and interface == 'wl_display' and op == 'get_registry':
            connection_starts += 1
        if sent and op in ('get_xdg_surface', 'get_subsurface', 'set_app_id',
                           'set_fullscreen', 'unset_fullscreen', 'set_title',
                           'set_content_type', 'set_presentation_hint',
                           'set_sync', 'set_desync', 'set_destination',
                           'set_source', 'get_fifo', 'set_barrier', 'wait_barrier'):
            metadata.append(f'{interface}#{oid}.{op}({args})')
        if sent and ((interface == 'zwp_linux_buffer_params_v1' and
                      op in ('add', 'create', 'create_immed')) or
                     (interface == 'wl_drm' and op in ('create_prime_buffer', 'create_buffer')) or
                     (interface == 'wl_region' and op in ('add', 'subtract'))):
            metadata.append(f'{interface}#{oid}.{op}({args})')
        if interface == 'wl_surface' and sent:
            state = surfaces[oid]
            if op == 'attach':
                state['attach_requests'] += 1
                state['pending_attach'] = any(k == 'wl_buffer' for k, _ in refs)
            elif op in ('damage', 'damage_buffer'):
                state['damage_requests'] += 1
                state['pending_damage'] = True
            elif op == 'frame':
                for kind, number in refs:
                    if kind == 'wl_callback':
                        callbacks[number] = oid
                        state['pending_callbacks'].append(number)
                        state['callback_requests'] += 1
            elif op == 'commit':
                state['commits'].append(now)
                if state['pending_attach'] or state['pending_damage']:
                    state['updates'].append(now)
                elif state['pending_callbacks'] or state['pending_feedback']:
                    state['callback_only'].append(now)
                else:
                    state['empty'].append(now)
                for number in state['pending_feedback']:
                    if number in feedbacks:
                        feedbacks[number]['latched'] = True
                        state['feedback_latched'] += 1
                state['pending_attach'] = state['pending_damage'] = False
                state['pending_callbacks'].clear()
                state['pending_feedback'].clear()
            elif op == 'set_opaque_region':
                state['opaque_regions'].append(args)
            elif op == 'set_buffer_scale':
                state['buffer_scales'].append(args)
            elif op == 'set_buffer_transform':
                state['transforms'].append(args)
        elif interface == 'wl_callback' and op == 'done' and not sent:
            owner = callbacks.pop(oid, None)
            if owner is not None:
                surfaces[owner]['callback_done'].append(now)
        elif interface == 'wp_presentation' and op == 'feedback' and sent:
            owner = next((n for k, n in refs if k == 'wl_surface'), None)
            number = next((n for k, n in refs if k == 'wp_presentation_feedback'), None)
            if owner is not None and number is not None:
                feedbacks[number] = {'surface': owner, 'requested_ms': now,
                                     'latched': False, 'replied': False}
                surfaces[owner]['feedback_requests'] += 1
                surfaces[owner]['pending_feedback'].append(number)
        elif interface == 'wp_presentation_feedback':
            feedback = feedbacks.get(oid)
            if feedback is None:
                continue
            state = surfaces[feedback['surface']]
            if not sent and op in ('presented', 'discarded'):
                feedback['replied'] = True
                state['feedback_' + op] += 1
                record = {'object': oid, 'event': op, 'latched': feedback['latched'],
                          'request_to_event_ms': now - feedback['requested_ms']}
                if op == 'presented':
                    values = [int(x.strip(), 0) for x in args.split(',')]
                    if len(values) == 7:
                        hi, lo, ns, refresh, seq_hi, seq_lo, flags = values
                        record.update(timestamp_ns=((hi << 32) | lo) * 1000000000 + ns,
                                      refresh_ns=refresh, sequence=(seq_hi << 32) | seq_lo,
                                      flags=flags, zero_copy=bool(flags & 8))
                state['feedback_records'].append(record)
            elif sent and op == 'destroy':
                if not feedback['replied']:
                    state['feedback_cancelled'] += 1
                state['pending_feedback'] = [n for n in state['pending_feedback'] if n != oid]
                feedbacks.pop(oid, None)
    result = {'protocol_lines': matches, 'registry_requests': connection_starts,
              'metadata': list(dict.fromkeys(metadata)), 'surfaces': {}}
    for number, state in sorted(surfaces.items()):
        presented = [r['timestamp_ns'] / 1000000 for r in state['feedback_records']
                     if 'timestamp_ns' in r]
        result['surfaces'][str(number)] = {
            'commits': cadence(state['commits']), 'buffer_or_damage': cadence(state['updates']),
            'callback_only_commits': cadence(state['callback_only']),
            'other_commits': cadence(state['empty']),
            'callback_requests': state['callback_requests'],
            'callback_done': cadence(state['callback_done']),
            'attach_requests': state['attach_requests'],
            'damage_requests': state['damage_requests'],
            'feedback_requests': state['feedback_requests'],
            'feedback_latched': state['feedback_latched'],
            'feedback_presented': state['feedback_presented'],
            'feedback_discarded': state['feedback_discarded'],
            'feedback_cancelled': state['feedback_cancelled'],
            'feedback_unreplied': sum(f['surface'] == number and not f['replied']
                                      for f in feedbacks.values()),
            'presentation_timestamps': cadence(presented),
            'feedback_records': state['feedback_records'],
            'opaque_regions': list(dict.fromkeys(state['opaque_regions'])),
            'buffer_scales': list(dict.fromkeys(state['buffer_scales'])),
            'transforms': list(dict.fromkeys(state['transforms'])),
        }
    return result


def describe(value):
    if not value['intervals']:
        return str(value['count']) + ' events (no intervals)'
    return (f"{value['count']} events; median {value['median_ms']:.3f} ms, "
            f"p95 {value['p95_ms']:.3f} ms, mean {value['mean_hz']:.3f} Hz")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('logs', nargs='+', type=pathlib.Path)
    parser.add_argument('--json', action='store_true')
    args = parser.parse_args()
    results = {}
    for path in args.logs:
        with path.open(encoding='utf-8', errors='replace') as stream:
            results[str(path)] = analyze(stream)
    if args.json:
        print(json.dumps(results, indent=2))
        return
    for path, result in results.items():
        print(f'\n== {path}: {result["protocol_lines"]} protocol lines ==')
        if not result['protocol_lines']:
            print('No Wayland protocol: cannot distinguish Xwayland from an empty/truncated capture.')
        if result['registry_requests'] > 1:
            print('NOTE: multiple registry requests; object IDs can collide across separate connections.')
        for entry in result['metadata']:
            print('  ' + entry)
        for number, state in result['surfaces'].items():
            print(f'  surface#{number}')
            for label in ('commits', 'buffer_or_damage', 'callback_only_commits',
                          'other_commits', 'callback_done', 'presentation_timestamps'):
                print(f'    {label}: {describe(state[label])}')
            print(f'    callbacks requested={state["callback_requests"]}, '
                  f'feedback requested={state["feedback_requests"]} '
                  f'latched={state["feedback_latched"]} '
                  f'presented={state["feedback_presented"]} '
                  f'discarded={state["feedback_discarded"]} '
                  f'cancelled={state["feedback_cancelled"]} '
                  f'unreplied={state["feedback_unreplied"]}')
            print(f'    opaque_regions={state["opaque_regions"]} '
                  f'buffer_scales={state["buffer_scales"]} transforms={state["transforms"]}')
    print('\nCallbacks and refresh_ns are NOT physical VRR measurements. '
          'KWin 6.7.5 omits ZERO_COPY even for scanout. '
          'Long captures need steady-state time slicing to exclude startup/shutdown; '
          'timestamps in separate processes are not a shared request correlation ID.')


if __name__ == '__main__':
    main()
