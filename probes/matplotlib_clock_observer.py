"""Diagnostic pytest plugin: observe real clock calls; never replace the clock/test.

Profiling changes execution overhead. These records are diagnostic evidence only,
never substituted for an official SWE-bench report.
"""
import hashlib
import inspect
import json
import os
from pathlib import Path
import sys
import time

import pytest

TARGET='test_warn_big_data_best_loc'


class Observer:
    def __init__(self,code,clock=time.perf_counter):
        self.code=code;self.clock=clock;self.calls=[];self.frames={}

    def __call__(self,frame,event,arg):
        if frame.f_code is not self.code:return
        if event=='call':
            row={'clock_condition_brackets':[]};self.calls.append(row);self.frames[id(frame)]=row
        else:
            row=self.frames.get(id(frame))
            if row is None:return
            if event in ('c_call','c_return') and arg is time.perf_counter and 'start_time' in frame.f_locals:
                # The actual native clock sample lies between these two probes.
                elapsed=self.clock()-frame.f_locals['start_time']
                if event=='c_call':row['clock_condition_brackets'].append({'lower_seconds':elapsed})
                else:row['clock_condition_brackets'][-1]['upper_seconds']=elapsed
            elif event=='return':
                loc=frame.f_locals
                row.update(loc_used_default=bool(loc['self']._loc_used_default),
                           badness=int(loc['badness']) if 'badness' in loc else None,
                           candidates=len(loc.get('candidates',[])),return_position=[float(x) for x in arg] if arg else None)
                self.frames.pop(id(frame),None)


@pytest.hookimpl(hookwrapper=True)
def pytest_runtest_call(item):
    if item.name!=TARGET:
        yield
        return
    if sys.getprofile() is not None:raise RuntimeError('Refuse to replace another profiler')
    module=sys.modules['matplotlib.legend'];fn=module.Legend._find_best_position
    paths={'test':Path(str(item.fspath)), 'legend':Path(module.__file__)}
    hashes=lambda:{k:hashlib.sha256(p.read_bytes()).hexdigest() for k,p in paths.items()}
    before=hashes();observer=Observer(fn.__code__)
    result={'test':item.nodeid,'file_hashes_before':before,
            'find_best_position_source':inspect.getsource(fn),
            'observer_uses_real_clock':True,'test_or_clock_monkeypatched':False}
    sys.setprofile(observer)
    try:
        outcome=yield
        result['test_call_exception']=str(outcome.excinfo[1]) if outcome.excinfo else None
    finally:
        sys.setprofile(None)
        result.update(calls=observer.calls,file_hashes_after=hashes())
        Path(os.environ['MPL_TIMING_OUTPUT']).write_text(json.dumps(result,indent=2)+'\n')
