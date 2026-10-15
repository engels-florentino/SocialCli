"""Topic selection keeps advanced contracts available without bloating bootstrap."""
from importlib.resources import files
import re

TOPICS={
    'setup':('Install the reviewed candidate','Authorize the installation, then the social account','Persist and recover','Publication gate'),
    'publish':('Publication gate','Temporary Instagram media','Canonical publishing workflow','Check supplied media locally'),
    'schedule':('Publication gate','Temporary Instagram media','Scheduling and temporary media','Native scheduling input examples (offline)'),
    'inbox':('Publication gate','Read metrics and prepare engagement','Read diagnostics','Comment API corrections'),
    'errors':('Persist and recover','Recovery and error actions','Read diagnostics','Comment API corrections'),
    'permissions':('Installation connection access',),
}


def guide(topic):
    if topic not in TOPICS and topic!='all':
        raise ValueError('Choose a guide topic: '+', '.join(TOPICS)+', all.')
    full=files('socialctl.ui').joinpath('assets/agent-manual.md').read_text(encoding='utf-8')
    if topic=='all':return full
    sections=re.split(r'(?=^## )',full,flags=re.MULTILINE)
    chosen=[section for section in sections if any(section.startswith('## '+heading) for heading in TOPICS[topic])]
    return ('# SocialCli agent guide: '+topic+'\n\n'+''.join(chosen)+
            '\nNext: socialcli agent-guide publish; socialcli agent-guide schedule; socialcli agent-guide inbox; socialcli agent-guide errors.\n')
