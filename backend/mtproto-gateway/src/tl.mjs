export const VECTOR_CONSTRUCTOR = 0x1cb5c415;

export class TLWriter {
  constructor() {
    this.parts = [];
    this.length = 0;
  }

  push(buffer) {
    const value = Buffer.from(buffer);
    this.parts.push(value);
    this.length += value.length;
    return this;
  }

  uint32(value) {
    const out = Buffer.allocUnsafe(4);
    out.writeUInt32LE(Number(value) >>> 0);
    return this.push(out);
  }

  int32(value) {
    const out = Buffer.allocUnsafe(4);
    out.writeInt32LE(Number(value) | 0);
    return this.push(out);
  }

  int64(value) {
    const out = Buffer.allocUnsafe(8);
    out.writeBigInt64LE(BigInt.asIntN(64, BigInt(value)));
    return this.push(out);
  }

  fixed(value, expectedLength = undefined) {
    const out = Buffer.from(value);
    if (expectedLength !== undefined && out.length !== expectedLength) {
      throw new RangeError(`Expected ${expectedLength} fixed bytes, got ${out.length}`);
    }
    return this.push(out);
  }

  bytes(value) {
    const data = Buffer.from(value);
    if (data.length > 0xffffff) {
      throw new RangeError('TL bytes exceed the 24-bit length limit');
    }

    const header = data.length < 254
      ? Buffer.from([data.length])
      : Buffer.from([254, data.length & 0xff, (data.length >>> 8) & 0xff, (data.length >>> 16) & 0xff]);
    this.push(header);
    this.push(data);
    const padding = (4 - ((header.length + data.length) & 3)) & 3;
    if (padding) this.push(Buffer.alloc(padding));
    return this;
  }

  string(value) {
    return this.bytes(Buffer.from(String(value), 'utf8'));
  }

  bool(value) {
    return this.uint32(value ? 0x997275b5 : 0xbc799737);
  }

  toBuffer() {
    return Buffer.concat(this.parts, this.length);
  }
}

export class TLReader {
  constructor(buffer) {
    this.buffer = Buffer.from(buffer);
    this.offset = 0;
  }

  get remaining() {
    return this.buffer.length - this.offset;
  }

  ensure(length) {
    if (!Number.isInteger(length) || length < 0 || this.remaining < length) {
      throw new RangeError('Truncated TL data');
    }
  }

  uint8() {
    this.ensure(1);
    return this.buffer[this.offset++];
  }

  uint32() {
    this.ensure(4);
    const value = this.buffer.readUInt32LE(this.offset);
    this.offset += 4;
    return value;
  }

  int32() {
    this.ensure(4);
    const value = this.buffer.readInt32LE(this.offset);
    this.offset += 4;
    return value;
  }

  int64() {
    this.ensure(8);
    const value = this.buffer.readBigInt64LE(this.offset);
    this.offset += 8;
    return value;
  }

  double() {
    this.ensure(8);
    const value = this.buffer.readDoubleLE(this.offset);
    this.offset += 8;
    return value;
  }

  fixed(length) {
    this.ensure(length);
    const value = this.buffer.subarray(this.offset, this.offset + length);
    this.offset += length;
    return value;
  }

  bytes(maxLength = 2 * 1024 * 1024) {
    const first = this.uint8();
    let length;
    let headerLength;
    if (first < 254) {
      length = first;
      headerLength = 1;
    } else if (first === 254) {
      this.ensure(3);
      length = this.buffer[this.offset]
        | (this.buffer[this.offset + 1] << 8)
        | (this.buffer[this.offset + 2] << 16);
      this.offset += 3;
      headerLength = 4;
    } else {
      throw new Error('Invalid TL bytes length marker');
    }

    if (length > maxLength) throw new RangeError('TL bytes exceed configured limit');
    const value = this.fixed(length);
    const padding = (4 - ((headerLength + length) & 3)) & 3;
    this.ensure(padding);
    this.offset += padding;
    return value;
  }

  string(maxLength) {
    return this.bytes(maxLength).toString('utf8');
  }

  expectEnd() {
    if (this.remaining !== 0) throw new Error(`Unexpected ${this.remaining} trailing TL bytes`);
  }
}

export function encodeTLBytes(value) {
  return new TLWriter().bytes(value).toBuffer();
}

export function concat(...values) {
  return Buffer.concat(values.map((value) => Buffer.from(value)));
}
