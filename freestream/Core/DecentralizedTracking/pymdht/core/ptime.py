﻿#Embedded file name: freestream\Core\DecentralizedTracking\pymdht\core\ptime.pyo
import sys
import time
sleep = time.sleep
if sys.platform == 'win32':
    time = time.clock
else:
    time = time.time
