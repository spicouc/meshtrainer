"""MUT-02: Replace SUPERSEDED SQL block with pass."""
lines = open('rc5_coordinator_ext.py').readlines()
for i, l in enumerate(lines):
    if "status='SUPERSEDED'" in l:
        indent = ' ' * (len(lines[i-1]) - len(lines[i-1].lstrip())) if i > 0 else ''
        del lines[i-1:i+2]
        lines.insert(i-1, indent + 'pass  # mutant: previous ACTIVE is not superseded\n')
        break
open('rc5_coordinator_ext.py', 'w').writelines(lines)
print('MUT-02 applied')
