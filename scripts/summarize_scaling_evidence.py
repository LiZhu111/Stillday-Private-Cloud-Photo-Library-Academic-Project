"""Summarize observed batch results without inferring concurrent-user capacity."""
import json
import statistics
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
def main():
    source = ROOT / 'evidence/cloud-batch-scaling.json'
    data = json.loads(source.read_text(encoding='utf-8'))
    samples = data['samples']
    accepted = [u for u in data['uploads'] if u.get('httpStatus') == 202]
    ready = next((s for s in samples if accepted and s.get('statuses', {}).get('READY') == len(accepted)), None)
    first_running = next((s for s in samples if s['running'] > 0), None)
    multiple = next((s for s in samples if s['running'] > 1), None)
    result = {
        'source': source.name, 'experimentStart': data['startedAt'],
        'acceptedPhotos': len(accepted), 'plannedPhotos': data['plannedImages'],
        'imagePixels': data['imagePixels'], 'bytesPerImage': data['bytesPerImage'],
        'uploaderConcurrency': data['uploaderConcurrency'],
        'medianCompletionRequestSeconds': statistics.median(u['completeSeconds'] for u in accepted) if accepted else None,
        'firstObservedWorkerRunning': first_running['time'] if first_running else None,
        'firstObservedAllReady': ready['time'] if ready else None,
        'firstObservedMultipleWorkersRunning': multiple['time'] if multiple else None,
        'maxObservedRunning': max((s['running'] for s in samples), default=0),
        'maxObservedDesired': max((s['desired'] for s in samples), default=0),
        'multipleWorkersObservedBeforeCompletion': bool(ready and multiple and multiple['time'] < ready['time']),
        'returnedToZero': data.get('allAcceptedReadyAndScaledToZero', False),
        'observationFinished': data.get('finishedAt'),
        'errors': data['errors'],
        'caveats': [
            'Samples are observations, not exact transition times.',
            'Approximate queue attributes may lag database status.',
            'This batch is not a 50-concurrent-user load test.',
            'Do not claim multi-worker throughput benefit when multiple workers started after completion.'
        ]
    }
    output = ROOT / 'evidence/cloud-batch-summary.json'
    output.write_text(json.dumps(result, indent=2), encoding='utf-8')
    print(json.dumps(result, indent=2))

if __name__ == '__main__':
    main()
