import { test } from 'node:test';
import assert from 'node:assert/strict';
import fs from 'node:fs';
import vm from 'node:vm';
import {createRequire} from 'node:module';
const require=createRequire(import.meta.url);
const ts=require('typescript');
const {QueryClient}=require('@tanstack/react-query');
const front=new URL('../',import.meta.url);
function load(rel,bindings){
 const code=fs.readFileSync(new URL('src/'+rel,front),'utf8').replace(/^import .*;\n/gm,'').replace(/^export \{.*\};\n/gm,'');
 const ctx=vm.createContext({exports:{},console,URLSearchParams,setTimeout,clearTimeout,...bindings});
 vm.runInContext(ts.transpileModule(code,{compilerOptions:{target:ts.ScriptTarget.ES2022,module:ts.ModuleKind.CommonJS}}).outputText,ctx);
 return ctx;
}
test('send rejects CONNECTING, succeeds OPEN, and does not duplicate connections',()=>{
 const sockets=[];
 class Socket {static OPEN=1;static CONNECTING=0;constructor(){this.readyState=0;this.frames=[];sockets.push(this)} send(s){this.frames.push(s)} close(){}}
 const ctx=load('lib/ws.ts',{WebSocket:Socket,useAuthStore:{getState:()=>({token:'t'})},getGatewayWebSocketUrl:()=> 'ws://test'});
 const ws=new ctx.exports.ChatWebSocket(()=>{});
 ws.connect(); ws.connect();
 assert.equal(sockets.length,1);
 assert.equal(ws.send('hello','session'),false);
 sockets[0].readyState=1;
 assert.equal(ws.send('hello','session'),true);
 assert.equal(sockets[0].frames.length,1);
 sockets[0].send=()=>{throw Error('closed')};
 assert.equal(ws.send('retry','session'),false);
});
test('workspace mutation invalidates exact gateway snapshot',async()=>{
 let mutation,query;
 const qc=new QueryClient();
 const api={get:async()=>({data:{content:'old'}}),put:async()=>({data:{content:'new'}})};
 const ctx=load('hooks/use-config.ts',{useGatewayBaseUrl:()=> 'http://a',useQuery:o=>{query=o},useMutation:o=>{mutation=o},useQueryClient:()=>qc,toast:{success(){}},i18n:{t:x=>x},api,gatewayApi:()=>api});
 ctx.exports.useWorkspaceFile('AGENTS.md'); const key=query.queryKey;
 qc.setQueryData(key,{content:'old'});
 ctx.exports.useSaveWorkspaceFile();
 const vars={name:'AGENTS.md',content:'new'};
 const data=await mutation.mutationFn(vars);
 await mutation.onSuccess(data,vars);
 assert.ok(qc.getQueryState(key).isInvalidated || qc.getQueryData(key).content==='new');
});

// Execute callbacks extracted from the real TSX, keeping their state transitions intact.
function callback(rel, match, bindings) {
 const source=ts.createSourceFile(rel,fs.readFileSync(new URL('src/'+rel,front),'utf8'),ts.ScriptTarget.Latest,true,ts.ScriptKind.TSX);
 let found;
 function visit(node) {
  if (match(node,source)) found=node.arguments[0];
  ts.forEachChild(node,visit);
 }
 visit(source);
 assert.ok(found,'callback exists');
 const code=ts.transpileModule('('+found.getText(source)+')',{compilerOptions:{target:ts.ScriptTarget.ES2022}}).outputText;
 return vm.runInNewContext(code,bindings);
}
function sendDraft(accepted, count=1, gateway='a') {
 const values=[],attachmentUpdates=[],errors=[];
 let sends=0;
 const fn=callback('components/chat/chat-input.tsx',n=>ts.isCallExpression(n)&&n.expression.getText()==='useCallback'&&n.parent.name?.getText()==='handleSend',{
  value:' draft ', gatewayBaseUrl:gateway, attachments:Array.from({length:count},(_,i)=>({gatewayBaseUrl:'a',name:'data.txt',url:'/file/'+i,attachmentId:String(i),uploading:false})),
  disabled:false,readOnly:false,isUploading:false,onSend:()=>{sends++;return accepted},
  setValue:v=>values.push(v),setAttachments:v=>attachmentUpdates.push(v),textareaRef:{current:null},toast:{error:s=>errors.push(s)}
 });
 fn(); return {values,attachmentUpdates,errors,sends};
}
test('rejected send preserves text and attachments; accepted send clears them once',()=>{
 const rejected=sendDraft(false);
 assert.equal(rejected.sends,1); assert.equal(rejected.values.length,0);assert.equal(rejected.attachmentUpdates.length,0);
 const accepted=sendDraft(true);
 assert.equal(accepted.sends,1);assert.deepEqual(accepted.values,['']);assert.equal(accepted.attachmentUpdates[0].length,0);
});
test('too many attachments preserves draft before submitting',()=>{
 const result=sendDraft(true,9);
 assert.equal(result.sends,0);assert.equal(result.values.length,0);assert.equal(result.errors.length,1);
});
test('history replacement handles hidden indices, shrinking history, and active streaming',()=>{
 const bindings={currentSessionKey:'s',historyLoaded:true,sessionStates:{},gatewayBaseUrl:'a',
  loadedKeyRef:{current:null},loadedRevisionRef:{current:null},lastSetMsgsRef:{current:[]},
  sessionMsgs:[{role:'tool',name:'message',content:'hidden',revision:'v1'},{role:'user',content:'keep',revision:'v1'}],
  nanoid:()=>String(Math.random()),useChatStore:{getState:()=>({messages:[]})},setMessages:m=>{bindings.output=m}};
 const run=()=>callback('pages/Chat.tsx',n=>ts.isCallExpression(n)&&n.expression.getText()==='useEffect'&&n.getText().includes('const snapshotKey'),bindings)();
 run(); assert.equal(bindings.output.length,1);assert.equal(bindings.output[0].serverIndex,1);
 bindings.sessionStates={s:{isWaiting:true}};bindings.sessionMsgs=[];run();assert.equal(bindings.output.length,1);
 bindings.sessionStates={};run();assert.equal(bindings.output.length,0);
 bindings.gatewayBaseUrl='b';bindings.sessionMsgs=[{role:'assistant',content:'B',revision:'v1'}];run();
 assert.equal(bindings.output[0].content,'B');
});

test('switching gateways cannot consume a draft with foreign attachments',()=>{
 const result=sendDraft(true,1,'b');
 assert.equal(result.sends,0);assert.equal(result.values.length,0);assert.equal(result.attachmentUpdates.length,0);
});
