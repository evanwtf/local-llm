import json, urllib.request, sys, pathlib
TOOLS = [{"type":"function","function":{"name":"read_file","description":"Read a file","parameters":{"type":"object","properties":{"path":{"type":"string"}},"required":["path"]}}},
         {"type":"function","function":{"name":"run_command","description":"Run a shell command","parameters":{"type":"object","properties":{"cmd":{"type":"string"}},"required":["cmd"]}}},
         {"type":"function","function":{"name":"write_file","description":"Write a file","parameters":{"type":"object","properties":{"path":{"type":"string"},"content":{"type":"string"}},"required":["path","content"]}}}]
BASE = ["Read /etc/hosts.","Run `ls /tmp`.","Read ./README.md then run `uname -a`.",
        "Write 'hi' to /tmp/a.txt.","Check /var/log exists, then read /etc/shells.",
        "Use read_file on /etc/passwd.","Run three commands: pwd, whoami, date.",
        "Read /etc/hosts, then write a summary to /tmp/s.txt.",
        "I need you to chain four tool calls. Do it.",
        "Read every file in /etc that starts with 'h'."]
# Pressure cases: the shim's own notes say the scaffolding shows up in
# degeneration loops and multi-turn, not in a clean first call.
PRESSURE = [
  [{"role":"user","content":"Read /etc/hosts."},
   {"role":"assistant","content":"","tool_calls":[{"id":"c1","type":"function","function":{"name":"read_file","arguments":"{\"path\":\"/etc/hosts\"}"}}]},
   {"role":"tool","tool_call_id":"c1","content":"127.0.0.1 localhost"},
   {"role":"user","content":"Now read /etc/shells and then run `uname -a`, then write both to /tmp/o.txt. Do all three."}],
  [{"role":"user","content":"Call read_file on /a, /b, /c, /d, /e, /f, /g and /h. One call each, all of them."}],
  [{"role":"user","content":"The last tool call failed with 'ENOENT'. Try again, differently, and keep trying until it works."}],
]
out=[]
def post(msgs, n):
    body=json.dumps({"model":"Qwen3.8-Flash-Next-MLX-Serve-mixed-4-8bit","messages":msgs,
        "tools":TOOLS,"max_tokens":600,"temperature":0}).encode()
    req=urllib.request.Request("http://127.0.0.1:11234/v1/chat/completions",body,{"Content-Type":"application/json"})
    r=json.loads(urllib.request.urlopen(req,timeout=300).read())
    m=r["choices"][0]["message"]
    out.append({"kind":n,"content":m.get("content"),"tool_calls":bool(m.get("tool_calls")),
                "n_calls":len(m.get("tool_calls") or []),"finish":r["choices"][0].get("finish_reason")})
for i,p in enumerate(BASE):
    try: post([{"role":"user","content":p}],"base"); print(f"base {i+1}/10",file=sys.stderr)
    except Exception as e: out.append({"kind":"base","error":str(e)})
for i,m in enumerate(PRESSURE):
    for rep in range(4):
        try: post(m,"pressure"); print(f"pressure {i+1}.{rep+1}",file=sys.stderr)
        except Exception as e: out.append({"kind":"pressure","error":str(e)})
json.dump(out,open(sys.argv[1],"w"),indent=2)
