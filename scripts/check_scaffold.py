"""Offline reference/tag/configuration checks, not CloudFormation validation."""
import ast
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def visit(value):
    if isinstance(value, dict):
        yield value
        for child in value.values():
            yield from visit(child)
    elif isinstance(value, list):
        for child in value:
            yield from visit(child)


def main():
    for path in (ROOT / 'photo_library').glob('*.py'):
        ast.parse(path.read_text(encoding='utf-8'), filename=str(path))
    count = 0
    for path in (ROOT / 'infrastructure').glob('*.template.json'):
        document = json.loads(path.read_text(encoding='utf-8'))
        names = set(document.get('Parameters', {})) | set(document['Resources'])
        for node in visit(document):
            if 'Ref' in node:
                assert node['Ref'] in names or node['Ref'].startswith('AWS::'), (path.name, node)
            if 'Fn::GetAtt' in node:
                assert node['Fn::GetAtt'][0] in document['Resources'], (path.name, node)
        for name, resource in document['Resources'].items():
            tags = resource['Properties'].get('Tags', [])
            if tags:
                assert {'Key':'purpose','Value':'assessment 3'} in tags, (path.name, name)
            count += 1
        assert 'assessment 2' not in path.read_text(encoding='utf-8')
        # The only A2 identifier permitted in generated templates is the explicitly
        # reused ECS cluster name. No A2 service, queue, table or bucket may leak in.
        template_text = path.read_text(encoding='utf-8').replace('n00000000-a2-cluster', '')
        assert 'n00000000-a2-' not in template_text
        if path.name == 'compute.template.json':
            assert document['Parameters']['CreateA3Cluster']['Default'] == 'false'
            assert document['Parameters']['ExistingClusterName']['Default'] == 'n00000000-a2-cluster'
            assert document['Resources']['Cluster']['Condition'] == 'UseDedicatedA3Cluster'
            for service in ('ApiService', 'WorkerService'):
                assert 'Fn::If' in document['Resources'][service]['Properties']['Cluster']
            environments = [entry for node in visit(document)
                            for entry in node.get('Environment', []) if isinstance(entry, dict)]
            env = {entry['Name']:entry['Value'] for entry in environments}
            assert env['REUSE_ASSIGNMENT2_RESOURCES'] == {'Ref':'ReuseAssignment2Resources'}
            assert env['DYNAMODB_LAYOUT'] == {'Fn::If':['ReuseA2Data','assignment2-classifications','pk-sk']}
            assert document['Outputs']['ClusterName']['Value'] == document['Resources']['ApiService']['Properties']['Cluster']
    print(f'Offline checks passed: Python syntax, JSON references and A3 tags across {count} resources.')
    print('These checks do not validate AWS schemas, IAM permissions or deployment.')


if __name__ == '__main__':
    main()

