(function(global){
    'use strict';

    const LIMITS = Object.freeze({nodes:5000, connections:20000});

    function deepClone(value){
        if(typeof structuredClone === 'function') return structuredClone(value);
        return JSON.parse(JSON.stringify(value));
    }

    function normalizeWorkflow(value){
        if(Array.isArray(value)) return {nodes:value, connections:[]};
        if(Array.isArray(value?.nodes)){
            return {nodes:value.nodes, connections:Array.isArray(value.connections) ? value.connections : []};
        }
        if(Array.isArray(value?.workflow?.nodes)){
            return {
                nodes:value.workflow.nodes,
                connections:Array.isArray(value.workflow.connections) ? value.workflow.connections : []
            };
        }
        return {nodes:[], connections:[]};
    }

    function assertLimits(nodes, connections){
        if(!Array.isArray(nodes) || !Array.isArray(connections)) throw new Error('工作流图数据格式不正确');
        if(nodes.length > LIMITS.nodes) throw new Error(`工作流节点不能超过 ${LIMITS.nodes} 个`);
        if(connections.length > LIMITS.connections) throw new Error(`工作流连线不能超过 ${LIMITS.connections} 条`);
    }

    function remapNodeReferences(node, idMap){
        ['items','children','nodeIds','inputNodeIds'].forEach(key => {
            if(Array.isArray(node[key])) node[key] = node[key].map(id => idMap.get(id) || id);
        });
        if(node.inputBindings && typeof node.inputBindings === 'object'){
            Object.values(node.inputBindings).forEach(binding => {
                if(binding?.sourceNodeId) binding.sourceNodeId = idMap.get(binding.sourceNodeId) || binding.sourceNodeId;
            });
        }
        return node;
    }

    function connectionKey(connection){
        return [
            connection?.from || '',
            connection?.to || '',
            connection?.kind || 'flow',
            connection?.fromPort || 'out',
            connection?.toPort || 'in'
        ].join('\u0000');
    }

    function appendConnection(connections, candidate, validator){
        if(!candidate?.from || !candidate?.to || candidate.from === candidate.to) return false;
        if(typeof validator === 'function' && validator(candidate) === false) return false;
        const key = connectionKey(candidate);
        if(connections.some(item => connectionKey(item) === key)) return false;
        connections.push(candidate);
        return true;
    }

    function snapshot(nodes, connections, extra={}){
        return {nodes:deepClone(nodes || []), connections:deepClone(connections || []), ...deepClone(extra)};
    }

    global.SynCanvasGraph = Object.freeze({
        LIMITS,
        deepClone,
        normalizeWorkflow,
        assertLimits,
        remapNodeReferences,
        connectionKey,
        appendConnection,
        snapshot
    });
})(window);
