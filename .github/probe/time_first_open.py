"""Time every request the page makes while the uncountable CSV is first opened."""
import os, sys, time, tempfile
sys.path.insert(0, sys.argv[1])            # tests/e2e_ide
os.chdir(os.path.join(sys.argv[1], "..", ".."))
import _harness as H
import test_table_view as TV
from playwright.sync_api import sync_playwright

tmp = tempfile.mkdtemp(prefix="tvprobe-")
t0 = time.time()
repo = H.make_repo(TV.build_uncountable, tmp)
print("fixture %.1fs" % (time.time() - t0), flush=True)
srv = H.IdeServer(repo)
print("server up %.1fs" % (time.time() - t0), flush=True)
pw = sync_playwright().start(); b = pw.chromium.launch(); p = b.new_page()
p.set_default_timeout(120000)
starts = {}
p.on("request", lambda r: starts.__setitem__(id(r), time.time()))
def done(r):
    rq = r.request
    print("  %6.2fs  %3d %s" % (time.time() - starts.get(id(rq), time.time()), r.status, rq.url.split("?")[0][-60:] + ("?" + rq.url.split("?")[1][:60] if "?" in rq.url else "")), flush=True)
p.on("response", done)
p.goto(srv.url)
t1 = time.time()
p.click("#qo-open"); p.wait_for_selector("#qo:not([hidden])"); p.fill("#qo-input", "data_huge.csv")
p.locator("#qo-results .qo-row", has_text="data_huge.csv").first.click()
print("clicked %.1fs" % (time.time() - t1), flush=True)
p.wait_for_selector("#view-navigate:not([hidden])")
print("NAVIGATE VISIBLE after %.1fs" % (time.time() - t1), flush=True)
p.wait_for_timeout(3000)
b.close(); pw.stop(); srv.stop()
