import os
import ptvsd

if os.environ.get('RUN_MAIN') or os.environ.get('WERKZEUG_RUN_MAIN'):
    ptvsd.enable_attach(address=('0.0.0.0', 5678), redirect_output=True)



