"use strict";
const test = require('node:test'), assert = require('node:assert/strict');
const fs = require('node:fs'), vm = require('node:vm'), {spawnSync} = require('node:child_process');
const map = require('../frontend/map-core.js');
const source = fs.readFileSync(require.resolve('leaflet/dist/leaflet-src.js'), 'utf8').replace(/\r/g, '');
/** Load the actual pinned vendor algorithm, without requiring a browser or copying its implementation. */
function vendorSource() {
  return ['simplify','_reducePoints','_simplifyDP','_simplifyDPStep','_sqDist','_sqClosestPointOnSegment'].map(name => {
    const start = source.indexOf('  function '+name+'(');
    assert.ok(start >= 0, 'Pinned Leaflet function exists: '+name);
    return source.slice(start,source.indexOf('\n  }',start)+4);
  }).join('\n');
}
const reference = vm.runInNewContext(vendorSource()+'\nsimplify');

test('iterative rendering preserves the actual Leaflet simplification output, order and point identities', () => {
  let seed = 42;
  const random = () => ((seed = (seed * 1664525 + 1013904223) >>> 0) / 2**32);
  const paths = [[], [{x:0,y:0}], [{x:0,y:0},{x:0,y:0}], Array.from({length:100},(_,i)=>({x:i,y:0}))];
  for(let path=0;path<50;path++) paths.push(Array.from({length:250},(_,i)=>({x:i+random(),y:random()*100})));
  for(const points of paths) for(const tolerance of [0,0.1,1,3,100]) {
    const before = JSON.stringify(points), expected = reference(points,tolerance), actual = map.simplify(points,tolerance);
    assert.deepEqual(Array.from(actual),Array.from(expected));
    assert.equal(JSON.stringify(points),before);
    assert.ok(actual.every(point=>points.includes(point)));
  }
});

test('a deep zigzag overflows the vendor recursion on a constrained stack while the iterative path completes', () => {
  const result = spawnSync(process.execPath,['--stack_size=128','-e',`
    const assert=require('node:assert/strict');
    ${vendorSource()}
    const points=Array.from({length:4000},(_,i)=>({x:i,y:i%2?10:0}));
    assert.throws(()=>simplify(points,0.1),RangeError);
    const safe=require(${JSON.stringify(require.resolve('../frontend/map-core.js'))}).simplify(points,0.1);
    assert.equal(safe.length,points.length);
    assert.equal(safe[0],points[0]); assert.equal(safe.at(-1),points.at(-1));
  `],{encoding:'utf8',timeout:15000});
  assert.equal(result.status,0,result.stderr);
});

test('custom polylines use the safe path on every redraw without patching vendor globals', () => {
  let builds=0;
  const leaflet={Polyline:{extend(methods) { builds++; return class {
    constructor(coords,options){this.coordinates=coords;this.options=options;Object.assign(this,methods);}
  };}}};
  const coords=[[40,14],[41,15]], line=map.polyline(leaflet,coords,{smoothFactor:1});
  assert.equal(line.coordinates,coords);
  for(const smoothFactor of [0,1,3]) {
    const points=Array.from({length:80},(_,i)=>({x:i,y:i%2}));
    line.options.smoothFactor=smoothFactor;line._parts=[points];line._simplifyPoints();
    assert.deepEqual(line._parts[0],Array.from(reference(points,smoothFactor)));
  }
  map.polyline(leaflet,coords,{smoothFactor:1});assert.equal(builds,1);
});

test('both single-trip and all-trip maps use the iterative polyline factory', () => {
  const component=fs.readFileSync(require.resolve('../frontend/components.jsx'),'utf8');
  assert.equal((component.match(/OBDMap\.polyline\(window\.L,/g)||[]).length,2);
  assert.doesNotMatch(component,/window\.L\.polyline\(/);
  assert.match(fs.readFileSync(require.resolve('../frontend/index.html'),'utf8'),/src="\/map-core\.js"/);
});
