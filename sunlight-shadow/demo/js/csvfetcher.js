function loadCSV(url) {
    return fetch(url).then((response) => response.text());
}

function parseLightingCSV(text) {
    const lines = text.trim().split('\n');
    lines.shift();

    const lightingMap = new Map();
    for (const line of lines) {
        const [tile, feature, triangle, , lighted] = line.split(';');
        const itile = parseInt(tile, 10);
        const ifeature = parseInt(feature, 10);
        const itriangle = parseInt(triangle, 10);

        if (!lightingMap.has(itile)) {
            lightingMap.set(itile, []);
        }

        const featureMap = lightingMap.get(itile);
        featureMap.push(lighted.trim().toLowerCase() === 'true');
    }
    return lightingMap;
}
