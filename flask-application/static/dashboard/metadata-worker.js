/* Read metadata off the UI thread; the image never leaves the browser. */
importScripts('../vendor/exifr/exifr.js');

function serializable(value, depth = 0) {
    if (depth > 12) return '[Nested metadata omitted]';
    if (ArrayBuffer.isView(value)) return {binaryBytes: value.byteLength};
    if (value instanceof ArrayBuffer) return {binaryBytes: value.byteLength};
    if (Array.isArray(value)) return value.map(item => serializable(item, depth + 1));
    if (value && typeof value === 'object') return Object.fromEntries(Object.entries(value).map(([key, item]) => [key, serializable(item, depth + 1)]));
    return typeof value === 'number' && !Number.isFinite(value) ? null : value;
}
function lookup(tags, names) {
    for (const name of names) {
        if (tags[name] !== undefined && tags[name] !== null && tags[name] !== '') return tags[name];
    }
    for (const group of Object.values(tags)) {
        if (group && typeof group === 'object' && !Array.isArray(group)) {
            const found = lookup(group, names);
            if (found !== undefined) return found;
        }
    }
}
function coordinate(value, reference) {
    if (Array.isArray(value)) value = Number(value[0]) + Number(value[1] || 0) / 60 + Number(value[2] || 0) / 3600;
    if (value === undefined || value === null || value === '') return null;
    const number = Number(value);
    return Number.isFinite(number) ? (['S', 'W'].includes(reference) ? -Math.abs(number) : number) : null;
}
async function jpegDimensions(file) {
    const bytes = new DataView(await file.slice(0, 1024 * 1024).arrayBuffer());
    if (bytes.byteLength < 4 || bytes.getUint16(0) !== 0xffd8) return {};
    let offset = 2;
    while (offset + 4 < bytes.byteLength) {
        if (bytes.getUint8(offset) !== 0xff) break;
        const marker = bytes.getUint8(offset + 1);
        if (marker === 0xda || marker === 0xd9) break;
        if (marker === 0xff) { offset++; continue; }
        const length = bytes.getUint16(offset + 2);
        if (length < 2 || offset + 2 + length > bytes.byteLength) break;
        if ([0xc0,0xc1,0xc2,0xc3,0xc5,0xc6,0xc7,0xc9,0xca,0xcb,0xcd,0xce,0xcf].includes(marker) && length >= 8) {
            return {height: bytes.getUint16(offset + 5), width: bytes.getUint16(offset + 7)};
        }
        offset += length + 2;
    }
    return {};
}
self.onmessage = async ({data: {file}}) => {
    try {
        const tags = await exifr.parse(file, {
            tiff: true, xmp: true, iptc: true, icc: true, jfif: true, ihdr: true,
            ifd0: true, exif: true, gps: true, interop: true, userComment: true,
            makerNote: true, mergeOutput: false, reviveValues: false, silentErrors: true,
        }) || {};
        const get = (...names) => lookup(tags, names);
        const dimensions = await jpegDimensions(file);
        const latitude = coordinate(get('latitude', 'GPSLatitude', 'GpsLatitude'), get('GPSLatitudeRef'));
        const longitude = coordinate(get('longitude', 'GPSLongitude', 'GpsLongitude', 'GpsLongtitude'), get('GPSLongitudeRef'));
        const summary = {
            width: get('ExifImageWidth', 'ImageWidth', 'ImageLengthX') ?? dimensions.width,
            height: get('ExifImageHeight', 'ImageHeight', 'ImageLength') ?? dimensions.height,
            captureTime: get('DateTimeOriginal', 'CreateDate', 'DateCreated'),
            captureTimeOffset: get('OffsetTimeOriginal', 'OffsetTimeDigitized'),
            cameraMake: get('Make'), cameraModel: get('Model'), lensModel: get('LensModel'),
            latitude: latitude !== null && Math.abs(latitude) <= 90 ? latitude : null,
            longitude: longitude !== null && Math.abs(longitude) <= 180 ? longitude : null,
            gpsAltitude: get('GPSAltitude'), gpsAltitudeReference: get('GPSAltitudeRef'),
            absoluteAltitude: get('AbsoluteAltitude'), relativeAltitude: get('RelativeAltitude'),
            iso: get('ISO', 'ISOSpeedRatings'), exposureTime: get('ExposureTime'), aperture: get('FNumber'),
            focalLength: get('FocalLength'), orientation: get('Orientation'),
            bands: get('SamplesPerPixel'), bitsPerSample: get('BitsPerSample', 'BitDepth'),
            flightYaw: get('FlightYawDegree'), flightPitch: get('FlightPitchDegree'), flightRoll: get('FlightRollDegree'),
            gimbalYaw: get('GimbalYawDegree'), gimbalPitch: get('GimbalPitchDegree'), gimbalRoll: get('GimbalRollDegree'),
            rtkFlag: get('RtkFlag'), rtkLatitudeStd: get('RtkStdLat'), rtkLongitudeStd: get('RtkStdLon'), rtkHeightStd: get('RtkStdHgt'),
            software: get('Software'), description: get('ImageDescription', 'description'), copyright: get('Copyright'),
        };
        self.postMessage({status: tags.errors?.length ? 'partial' : 'ready', summary: serializable(summary), tags: serializable(tags)});
    } catch (error) {
        self.postMessage({status: 'error', message: 'Metadata could not be read. You can still select this image for upload.', summary: {}, tags: {}});
    }
};
