"""Regression: a fresh Publii 0.48.1 site has fewer than 40 UIA elements."""
from types import SimpleNamespace
import pytest
from publii_toolkit.desktop import DesktopError, PubliiDesktop
import publii_toolkit.desktop as desktop

class Element:
 def __init__(self,text,kind):self.text=text;self.element_info=SimpleNamespace(control_type=kind)
 def window_text(self):return self.text

@pytest.mark.parametrize('preview',['Preview site','Preview your changes'])
def test_small_usable_sidebar_is_ready(tmp_path,preview):
 driver=PubliiDesktop(sites_root=tmp_path,executable=tmp_path/'Publii.exe')
 window=SimpleNamespace(descendants=lambda:[Element('Pages','Hyperlink'),Element('Sync your website','Hyperlink'),Element(preview,'Button')])
 driver._wait_for_tree(window,timeout_s=1)

def test_inert_small_window_is_rejected(tmp_path,monkeypatch):
 driver=PubliiDesktop(sites_root=tmp_path,executable=tmp_path/'Publii.exe')
 ticks=iter([0,0,2]);monkeypatch.setattr(desktop.time,'time',lambda:next(ticks));monkeypatch.setattr(desktop.time,'sleep',lambda _:None)
 window=SimpleNamespace(descendants=lambda:[Element('Publii','Window'),Element('Pages','Text'),Element('Sync your website','Text')])
 with pytest.raises(DesktopError,match='accessibility tree'):
  driver._wait_for_tree(window,timeout_s=1)

def test_terminal_title_cannot_be_selected_as_publii(tmp_path,monkeypatch):
 import sys
 driver=PubliiDesktop(sites_root=tmp_path,executable=tmp_path/'Publii.exe')
 terminal=SimpleNamespace(window_text=lambda:'Prepare Publii | Websites',process_id=lambda:777)
 imposter=SimpleNamespace(window_text=lambda:'Publii',process_id=lambda:778)
 real=SimpleNamespace(window_text=lambda:'Publii',process_id=lambda:1234)
 monkeypatch.setattr(desktop.subprocess,'run',lambda *a,**k:SimpleNamespace(stdout='"Publii.exe","1234","Console","1","10,000 K"'))
 monkeypatch.setitem(sys.modules,'pywinauto',SimpleNamespace(Desktop=lambda **k:SimpleNamespace(windows=lambda:[terminal,imposter,real])))
 assert driver._find_window(timeout_s=1) is real

def test_tree_is_read_without_taking_focus(tmp_path):
 # 2026-11-12: a locked session refuses the foreground, and the tree reads without it.
 driver=PubliiDesktop(sites_root=tmp_path,executable=tmp_path/'Publii.exe')
 class LazyWindow:
  def set_focus(self):raise AssertionError('focus is never needed')
  def descendants(self):
   return [Element('Pages','Hyperlink'),Element('Sync your website','Hyperlink'),Element('Preview site','Button')]
 driver._wait_for_tree(LazyWindow(),timeout_s=1)
