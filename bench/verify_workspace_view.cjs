// Optional DOM test: npm install jsdom; node bench/verify_workspace_view.cjs HTML OUTPUT_JSON [JSDOM_PATH]
const fs=require('fs'),assert=require('assert');
const {JSDOM,VirtualConsole}=require(process.argv[4]||'jsdom');
const html=fs.readFileSync(process.argv[2],'utf8'),errors=[];
const console_=new VirtualConsole();console_.on('jsdomError',e=>errors.push(e.message));
const dom=new JSDOM(html,{runScripts:'dangerously',virtualConsole:console_,url:'file:///grapholog/map.html'});
const d=dom.window.document,data=JSON.parse(d.getElementById('workspace-data').textContent);
assert.equal(errors.length,0,errors.join('\n'));assert.ok(d.querySelectorAll('.group').length>0);
assert.equal(d.querySelectorAll('svg').length,1);assert.ok(d.querySelectorAll('svg rect').length>2);
const firstSvg=new dom.window.XMLSerializer().serializeToString(d.querySelector('svg'));
const search=d.getElementById('search');search.value='no-such-function-xx';search.dispatchEvent(new dom.window.Event('input'));
assert.equal(d.querySelectorAll('.group').length,0);assert.equal(d.querySelectorAll('svg').length,0);
search.value='';search.dispatchEvent(new dom.window.Event('input'));
const buttons=d.querySelectorAll('.group');buttons[buttons.length-1].click();assert.equal(d.querySelectorAll('.group[aria-current=true]').length,1);
assert.ok(d.querySelector('code').textContent.includes('grapholog.request.v1'));
const project=d.getElementById('project');project.value=data.projects[data.projects.length-1].id;project.dispatchEvent(new dom.window.Event('input'));
assert.equal(d.querySelectorAll('.group').length,data.groups.filter(g=>g.project_count>1 && project.value in g.project_counts).length);
const scope=d.getElementById('scope');scope.value='all';scope.dispatchEvent(new dom.window.Event('input'));
assert.equal(errors.length,0,errors.join('\n'));
const result={dom_execution:'passed',svg_rendering:'passed',search_empty_reset:'passed',group_selection:'passed',
  full_project_membership_filter:'passed',scope_filter:'passed',javascript_errors:errors,pixel_visual_inspection:false};
fs.writeFileSync(process.argv[3],JSON.stringify(result,null,2)+'\n');
fs.writeFileSync(process.argv[3].replace(/\.json$/,'.svg'),firstSvg);
console.log(JSON.stringify(result));dom.window.close();
