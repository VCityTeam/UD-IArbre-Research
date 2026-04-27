function applyLighting(mesh, tileid, triangleShade) {
    const geometry = mesh.geometry;
    const position = geometry.attributes.position;
    const featureIDS = geometry.attributes._BATCHID;
    const triangleCount = position.count / 3;

    console.debug('Number of triangles : ', triangleCount);
    console.debug('BatchIDS : ', featureIDS);

    const colors = new Float32Array(position.count * 3);
    for (let i = 0; i < triangleCount; i++) {
        let color;
        if (triangleShade && triangleShade[i]) {
            color = new itowns.THREE.Color(1.0, 0.9, 0.01);
        } else {
            color = new itowns.THREE.Color(0.2, 0.2, 0.2);
        }

        for (let j = 0; j < 3; j++) {
            const index = (i * 3 + j) * 3;
            colors[index] = color.r;
            colors[index + 1] = color.g;
            colors[index + 2] = color.b;
        }
    }
    geometry.setAttribute('color', new itowns.THREE.BufferAttribute(colors, 3));

    const sunlightMaterial = new itowns.THREE.ShaderMaterial({
        vertexShader: `
            #include <itowns/precision_qualifier>
            #include <common>
            #include <logdepthbuf_pars_vertex>

            out vec3 vColor;

            void main() {
            #include <begin_vertex>
            #include <project_vertex>
            #include <logdepthbuf_vertex>
                vColor = color;
                gl_Position = projectionMatrix * modelViewMatrix * vec4(position, 1.0);
            }
        `,
        fragmentShader: `
            #include <itowns/precision_qualifier>
            #include <logdepthbuf_pars_fragment>

            in vec3 vColor;

            void main() {
                #include <logdepthbuf_fragment>
                gl_FragColor = vec4(vColor, 1.0);
            }
        `,
        vertexColors: true,
        transparent: false,
        side: itowns.THREE.FrontSide
    });
    mesh.material = sunlightMaterial;
}
