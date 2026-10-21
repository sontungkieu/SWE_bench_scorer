"use strict";
const $ = id => document.getElementById(id);
let config = null, filePayload = null, validated = null, selectedJob = null, submitting = false, validationGeneration = 0;
const statusNames = {queued:"Đang chờ",running:"Đang chạy",completed:"Hoàn tất",failed:"Lỗi / incomplete",interrupted:"Bị gián đoạn"};
const formatTime = value => value ? new Date(value).toLocaleString("vi-VN", {hour12:false}) : "—";
async function api(path, options={}) {const response = await fetch(path, options);const value = await response.json();if(!response.ok) throw new Error(value.error || "Không thể kết nối server");return value;}
function toast(message) {$("toast").textContent=message;$("toast").classList.remove("hidden");setTimeout(()=>$("toast").classList.add("hidden"),6500);}
function mode() {return document.querySelector('input[name="mode"]:checked').value;}
function updatePlan() {
  document.querySelectorAll(".mode").forEach(label=>label.classList.toggle("active",label.querySelector("input").checked));
  const full=mode()==="full";
  $("instanceIds").disabled=full;
  $("estimate").textContent=full ? "$"+Number(config?.full_estimate_usd || 20).toFixed(2) : "$0.50";
  $("fullHint").textContent=full ? "500 bài, một worker. Kiểm tra ngân sách trước mỗi job; có thể chạy nhiều giờ." : "Một worker CPU; mỗi job smoke có thời hạn tối đa 30 phút.";
  const ready=validated && (!full || validated.can_full) && !submitting;
  $("evaluateButton").disabled=!ready;
  $("evaluateButton").textContent=submitting ? "Đang tạo job…" : !validated ? "Chọn file để bắt đầu" : full && !validated.can_full ? "Full cần đủ 500 predictions" : full ? "Chấm đủ 500 bài →" : "Chấm smoke test →";
}
async function chooseFile(file) {
  if(!file) return;
  const generation=++validationGeneration;
  validated=null;filePayload=null;updatePlan();
  $("fileName").textContent=file.name;$("fileSize").textContent=(file.size/1024).toFixed(1)+" KB · "+file.name.split(".").pop().toUpperCase();
  $("dropzone").classList.add("hidden");$("fileInfo").classList.remove("hidden");$("inputStats").classList.add("hidden");
  $("validation").className="validation muted";$("validation").textContent="Đang kiểm tra định dạng và các trường bắt buộc…";
  try {
    if(file.size>20*1024*1024) throw new Error("File vượt giới hạn 20 MiB");
    const payload={filename:file.name,content:new TextDecoder("utf-8",{fatal:true}).decode(await file.arrayBuffer())};
    const preview=await api("/api/validate",{method:"POST",headers:{"Content-Type":"application/json"},body:JSON.stringify(payload)});
    if(generation!==validationGeneration) return;
    filePayload=payload;validated=preview;
    $("validation").className="validation success";$("validation").textContent="✓ Định dạng hợp lệ · sẵn sàng gửi sang official harness";
    $("count").textContent=preview.count;$("emptyCount").textContent=preview.empty_count;$("model").textContent=preview.model;
    $("inputStats").classList.remove("hidden");
    $("instanceIds").value="";$("instanceIds").placeholder=preview.instance_ids.slice(0,3).join("\n");
  } catch(error) {if(generation!==validationGeneration)return;$("validation").className="validation error";$("validation").textContent=error.message;}
  updatePlan();
}
function clearFile(){validationGeneration++;filePayload=null;validated=null;$("fileInput").value="";$("dropzone").classList.remove("hidden");$("fileInfo").classList.add("hidden");$("inputStats").classList.add("hidden");$("validation").className="validation muted";$("validation").textContent="Chưa có file. Dữ liệu được kiểm tra trước khi tạo job.";updatePlan();}
async function submit(mock=false){
  if(submitting)return;
  const selectedMode=mock?"mock":mode();
  if(selectedMode==="full"&&!window.confirm("Chạy full 500 bài? Guard sẽ kiểm tra dự trù $"+config.full_estimate_usd+"; đây không phải hard billing cap. Job có thể chạy nhiều giờ."))return;
  submitting=true;$("mockButton").disabled=true;updatePlan();
  try{
    const payload={...(mock?{}:filePayload),mode:selectedMode,request_id:crypto.randomUUID()};
    if(!mock&&selectedMode==="smoke"&&$("instanceIds").value.trim())payload.instance_ids=$("instanceIds").value.trim().split(/[\s,]+/);
    const result=await api("/api/jobs",{method:"POST",headers:{"Content-Type":"application/json"},body:JSON.stringify(payload)});
    selectedJob=result.id;localStorage.setItem("swe-selected-job",selectedJob);$("logDetails").open=true;
    toast("Đã tạo job. Bạn có thể đóng tab và quay lại sau.");await refresh();$("results").scrollIntoView({behavior:"smooth",block:"start"});
  }catch(error){toast(error.message);}finally{submitting=false;$("mockButton").disabled=false;updatePlan();}
}
function addMetric(label,value,note,kind=""){
  const card=document.createElement("div");card.className="result-card "+kind;
  for(const [cls,text] of [["metric-label",label],["metric-value",value],["metric-note",note]]){const node=document.createElement("div");node.className=cls;node.textContent=text;card.append(node);}
  $("resultCards").append(card);
}
async function showJob(id){
  const job=await api("/api/jobs/"+encodeURIComponent(id));
  $("emptyResult").classList.add("hidden");$("activeResult").classList.remove("hidden");
  $("runStatus").textContent=statusNames[job.status]||job.status;$("runStatus").className="status "+job.status;
  $("runId").textContent=job.id;$("runTime").textContent=formatTime(job.started||job.created);
  $("downloadArtifact").classList.toggle("hidden",!job.artifacts_ready);$("downloadArtifact").href="/api/jobs/"+encodeURIComponent(id)+"/artifact";
  $("resultCards").replaceChildren();
  const summaries=job.summaries||{};
  if(job.mode==="mock"){
    for(const label of ["gold","wrong","empty"]){const s=summaries[label];const correct=s&&s.complete&&s.harness_exit_code===0&&s.resolved_count===(label==="gold"?1:0);addMetric(label.toUpperCase(),s?(s.resolved_count?"Resolved":"Unresolved"):"—",s?(correct?"Đúng kỳ vọng":"Cần kiểm tra report"):"Đang chờ kết quả",s?(correct?"good":"bad"):"");}
    $("runExplanation").textContent="Mock: ba control của cùng một bài. Đây là kiểm tra pipeline, không phải điểm model hay ba bài độc lập.";
  }else if(summaries.evaluation){
    const s=summaries.evaluation;const rate=job.mode==="full"?s.verified_score:s.subset_resolved_rate;
    addMetric(job.mode==="full"?"VERIFIED SCORE":"SUBSET RATE",rate===null?"—":(rate*100).toFixed(1)+"%",s.complete?"Report đầy đủ":"Incomplete · chưa có điểm hợp lệ",s.complete?"good":"bad");
    addMetric("RESOLVED",s.resolved_count+" / "+s.selected_count,"Patch rỗng vẫn nằm trong mẫu số");addMetric("ERROR / MISSING",s.error_or_missing_report_count,"Xem log để phân biệt lỗi test và hạ tầng",s.error_or_missing_report_count?"bad":"");
    $("runExplanation").textContent=job.mode==="full"?"Full Verified dùng mẫu số 500. Không coi exit code 0 là đủ nếu còn thiếu report.":"Smoke chỉ tính trên subset đã chọn; chưa phải điểm SWE-bench Verified 500 bài.";
  }else{
    addMetric("INPUT",job.count,"predictions trong file");addMetric("SELECTED",job.selected.length,"instance được chọn");addMetric("STATE",statusNames[job.status]||job.status,job.status==="running"?"Có thể mất vài phút để pull image":"Xem live log");
    $("runExplanation").textContent=job.status==="interrupted"?"Worker bị gián đoạn. Server không tự chạy lại job trả phí; Modal VM có lifetime hữu hạn.":"Input được chuẩn hóa thành JSONL trước khi gửi Modal. Kết quả chỉ xuất hiện sau khi harness trả report.";
  }
  const log=await api("/api/jobs/"+encodeURIComponent(id)+"/log");const pre=$("liveLog");const nearBottom=pre.scrollHeight-pre.scrollTop-pre.clientHeight<60;pre.textContent=log.text;if(nearBottom)pre.scrollTop=pre.scrollHeight;
}
async function refresh(){
  const result=await api("/api/jobs");$("historyCount").textContent=result.jobs.length+" jobs";
  const tbody=$("historyBody");tbody.replaceChildren();
  if(!result.jobs.length){const row=document.createElement("tr"),cell=document.createElement("td");cell.colSpan=5;cell.className="table-empty";cell.textContent="Chưa có job nào trong workspace.";row.append(cell);tbody.append(row);}
  for(const job of result.jobs){const tr=document.createElement("tr");tr.className="job-row";tr.tabIndex=0;const first=document.createElement("td");const name=document.createElement("span");name.className="job-name";name.textContent=job.filename;const iid=document.createElement("span");iid.className="job-id";iid.textContent=job.id;first.append(name,iid);tr.append(first);
    for(const text of [job.mode.toUpperCase(),job.count+" preds"]){const td=document.createElement("td");td.textContent=text;tr.append(td);}const state=document.createElement("td"),badge=document.createElement("span");badge.className="status "+job.status;badge.textContent=statusNames[job.status]||job.status;state.append(badge);tr.append(state);const time=document.createElement("td");time.textContent=formatTime(job.created);tr.append(time);
    const choose=async()=>{selectedJob=job.id;localStorage.setItem("swe-selected-job",selectedJob);await showJob(job.id);};tr.addEventListener("click",()=>choose().catch(e=>toast(e.message)));tr.addEventListener("keydown",e=>{if(e.key==="Enter")choose().catch(err=>toast(err.message));});tbody.append(tr);}
  if(!selectedJob&&result.jobs.length)selectedJob=result.jobs[0].id;
  if(selectedJob&&result.jobs.some(j=>j.id===selectedJob))await showJob(selectedJob);
}
$("fileInput").addEventListener("change",e=>chooseFile(e.target.files[0]));$("clearFile").addEventListener("click",clearFile);$("evaluateButton").addEventListener("click",()=>submit());$("mockButton").addEventListener("click",()=>submit(true));$("refreshHistory").addEventListener("click",()=>refresh().catch(e=>toast(e.message)));
document.querySelectorAll('input[name="mode"]').forEach(input=>input.addEventListener("change",updatePlan));
const dz=$("dropzone");dz.addEventListener("dragover",e=>{e.preventDefault();dz.classList.add("dragging");});dz.addEventListener("dragleave",()=>dz.classList.remove("dragging"));dz.addEventListener("drop",e=>{e.preventDefault();dz.classList.remove("dragging");chooseFile(e.dataTransfer.files[0]);});dz.addEventListener("keydown",e=>{if(e.key==="Enter"||e.key===" "){$("fileInput").click();e.preventDefault();}});
(async()=>{try{config=await api("/api/config");$("version").textContent="v"+config.version;$("footerVersion").textContent="v"+config.version;$("profile").textContent=config.profile;if(!config.allow_full){$("fullOption").classList.add("disabled");$("fullOption").querySelector("input").disabled=true;}selectedJob=localStorage.getItem("swe-selected-job");updatePlan();await refresh();setInterval(()=>refresh().catch(()=>{}),3000);}catch(error){toast(error.message);}})();
