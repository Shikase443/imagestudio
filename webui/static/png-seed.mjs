// Read the actual seed written by sd-server; never infer it from the request.
export function pngSeed(bytes) {
  const signature = [137,80,78,71,13,10,26,10];
  if (bytes.length < 8 || signature.some((value,index)=>bytes[index] !== value)) return null;
  const view = new DataView(bytes.buffer,bytes.byteOffset,bytes.byteLength);
  const decoder = new TextDecoder();
  for (let offset=8; offset+12<=bytes.length;) {
    const length = view.getUint32(offset);
    if (length > bytes.length-offset-12) return null;
    const type = decoder.decode(bytes.subarray(offset+4,offset+8));
    if (type === 'tEXt') {
      const chunk = bytes.subarray(offset+8,offset+8+length);
      const separator = chunk.indexOf(0);
      if (separator >= 0 && decoder.decode(chunk.subarray(0,separator)) === 'parameters') {
        const text = decoder.decode(chunk.subarray(separator+1));
        const marker = text.lastIndexOf('SDCPP: ');
        if (marker >= 0) {
          try {
            const seed = JSON.parse(text.slice(marker+7)).seed;
            if (Number.isSafeInteger(seed) && seed >= 0) return String(seed);
          } catch { /* Missing metadata must not prevent displaying the image. */ }
        }
      }
    }
    if (type === 'IEND') break;
    offset += length+12;
  }
  return null;
}
