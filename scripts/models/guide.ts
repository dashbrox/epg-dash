import { Collection, Logger } from '@freearhey/core'
import { Storage } from '@freearhey/storage-js'
import { Channel, Program } from '.'
import utc from 'dayjs/plugin/utc'
import dayjs from 'dayjs'
import path from 'node:path'
import pako from 'pako'

dayjs.extend(utc)

interface GuideData {
  channels: Collection<Channel>
  programs: Collection<Program>
  filepath: string
  gzip: boolean
}

export class Guide {
  channels: Collection<Channel>
  programs: Collection<Program>
  filepath: string
  gzip: boolean

  constructor(data: GuideData) {
    this.channels = data.channels
    this.programs = data.programs
    this.filepath = data.filepath
    this.gzip = data.gzip || false
  }

  addChannel(channel: Channel) {
    this.channels.add(channel)
  }

  toString() {
    const currDate = dayjs.utc(process.env.CURR_DATE || new Date().toISOString())

    let output = `<?xml version="1.0" encoding="UTF-8"?>\n`
    output += `<tv generator-info-name="custom-epg" date="${formatXMLTVDate(currDate)}">\n`

    const uniqueChannels = this.channels.uniqBy((channel: Channel) => channel.xmltv_id || channel.site_id)

    uniqueChannels.forEach((channel: Channel) => {
      const id = escapeXml(channel.xmltv_id || channel.site_id || '')
      if (!id) return

      output += `  <channel id="${id}">\n`

      if (channel.name) {
        output += `    <display-name>${escapeXml(channel.name)}</display-name>\n`
      }

      if (channel.logo) {
        output += `    <icon src="${escapeXml(channel.logo)}"/>\n`
      }

      output += `  </channel>\n`
    })

    this.programs.forEach((program: Program) => {
      output += buildProgramme(program)
    })

    output += `</tv>\n`

    return output
  }

  async save({ logger }: { logger: Logger }) {
    const dir = path.dirname(this.filepath)
    const storage = new Storage(dir)
    const xmlFilepath = this.filepath
    const xmlFilename = path.basename(xmlFilepath)
    logger.info(`  saving to "${xmlFilepath}"...`)
    const xmltv = this.toString()
    await storage.save(xmlFilename, xmltv)

    if (this.gzip) {
      const compressed = pako.gzip(xmltv)
      const gzFilepath = `${this.filepath}.gz`
      const gzFilename = path.basename(gzFilepath)
      logger.info(`  saving to "${gzFilepath}"...`)
      await storage.save(gzFilename, compressed)
    }
  }
}

function buildProgramme(program: Program): string {
  const p = toPlainProgram(program)

  const channelId = escapeXml(getChannelId(p))
  const start = formatXMLTVDate(p.start)
  const stop = formatXMLTVDate(p.stop)

  let output = `  <programme start="${start}" stop="${stop}" channel="${channelId}">\n`

  // 1) titles
  const titles = getTitles(p)
  titles.forEach(title => {
    const langAttr = title.lang ? ` lang="${escapeXml(title.lang)}"` : ''
    output += `    <title${langAttr}>${escapeXml(title.value)}</title>\n`
  })

  // 2) desc
  const desc = getDesc(p)
  if (desc?.value) {
    const langAttr = desc.lang ? ` lang="${escapeXml(desc.lang)}"` : ' lang="es"'
    output += `    <desc${langAttr}>${escapeXml(desc.value)}</desc>\n`
  }

  // 3) sub-title
  const subTitle = getSubTitle(p)
  if (subTitle?.value) {
    const langAttr = subTitle.lang ? ` lang="${escapeXml(subTitle.lang)}"` : ' lang="es"'
    output += `    <sub-title${langAttr}>${escapeXml(subTitle.value)}</sub-title>\n`
  }

  // 4) category
  const categories = getCategories(p)
  categories.forEach(category => {
    const langAttr = category.lang ? ` lang="${escapeXml(category.lang)}"` : ' lang="es"'
    output += `    <category${langAttr}>${escapeXml(category.value)}</category>\n`
  })

  // 5) date
  const year = getYear(p)
  if (year) {
    output += `    <date>${year}</date>\n`
  }

  // 6) episode-num
  const episodeNumbers = getEpisodeNumbers(p)
  episodeNumbers.forEach(ep => {
    const systemAttr = ep.system ? ` system="${escapeXml(ep.system)}"` : ''
    output += `    <episode-num${systemAttr}>${escapeXml(ep.value)}</episode-num>\n`
  })

  // 7) rating
  const rating = getRating(p)
  if (rating) {
    output += `    <rating><value>${escapeXml(rating)}</value></rating>\n`
  }

  // 8) image
  const image = getImage(p)
  if (image) {
    output += `    <image>${escapeXml(image)}</image>\n`
  }

  output += `  </programme>\n`
  return output
}

function toPlainProgram(program: Program): any {
  if (program && typeof (program as any).toObject === 'function') {
    return (program as any).toObject()
  }
  return program as any
}

function getChannelId(p: any): string {
  return p.channel || p.channelId || ''
}

function getTitles(p: any): Array<{ value: string; lang?: string }> {
  if (Array.isArray(p.titles) && p.titles.length) {
    return p.titles
      .filter((t: any) => t && t.value)
      .map((t: any) => ({ value: String(t.value), lang: t.lang ? String(t.lang) : undefined }))
  }

  const out: Array<{ value: string; lang?: string }> = []

  if (p.title_es || p.title) out.push({ value: String(p.title_es || p.title), lang: 'es' })
  if (p.title_en) out.push({ value: String(p.title_en), lang: 'en' })

  return out
}

function getDesc(p: any): { value: string; lang?: string } | null {
  if (Array.isArray(p.descs) && p.descs.length) {
    const item = p.descs.find((d: any) => d?.value) || p.descs[0]
    if (item?.value) return { value: String(item.value), lang: item.lang ? String(item.lang) : undefined }
  }

  const value = p.description || p.synopsis || p.desc
  return value ? { value: String(value), lang: 'es' } : null
}

function getSubTitle(p: any): { value: string; lang?: string } | null {
  if (Array.isArray(p.subTitles) && p.subTitles.length) {
    const item = p.subTitles.find((s: any) => s?.value) || p.subTitles[0]
    if (item?.value) return { value: String(item.value), lang: item.lang ? String(item.lang) : undefined }
  }

  const value = p.sub_title || p.subtitle || p.episode_title
  return value ? { value: String(value), lang: 'es' } : null
}

function getCategories(p: any): Array<{ value: string; lang?: string }> {
  if (Array.isArray(p.categories) && p.categories.length) {
    return p.categories
      .filter((c: any) => c && c.value)
      .map((c: any) => ({ value: String(c.value), lang: c.lang ? String(c.lang) : undefined }))
  }

  if (p.category) {
    return [{ value: String(p.category), lang: 'es' }]
  }

  return []
}

function getYear(p: any): string | null {
  return normalizeYear(p.year || p.date)
}

function getEpisodeNumbers(p: any): Array<{ system?: string; value: string }> {
  if (Array.isArray(p.episodeNumbers) && p.episodeNumbers.length) {
    return p.episodeNumbers
      .filter((e: any) => e && e.value)
      .map((e: any) => ({ system: e.system ? String(e.system) : undefined, value: String(e.value) }))
  }

  const season = toInteger(p.season)
  const episode = toInteger(p.episode)

  if (season !== null && episode !== null) {
    return [
      { system: 'xmltv_ns', value: `${season - 1}.${episode - 1}.0/1` },
      { system: 'onscreen', value: `S${String(season).padStart(2, '0')}E${String(episode).padStart(2, '0')}` }
    ]
  }

  if (episode !== null) {
    return [{ system: 'onscreen', value: `E${String(episode).padStart(2, '0')}` }]
  }

  return []
}

function getRating(p: any): string | null {
  if (Array.isArray(p.ratings) && p.ratings.length) {
    const first = p.ratings[0]
    if (first?.value) return String(first.value)
  }

  if (p.rating !== null && p.rating !== undefined && p.rating !== '') {
    return String(p.rating)
  }

  return null
}

function getImage(p: any): string | null {
  if (Array.isArray(p.images) && p.images.length) {
    const first = p.images[0]
    if (typeof first === 'string') return first
    if (first?.value) return String(first.value)
    if (first?.src) return String(first.src)
  }

  if (p.icon) {
    if (typeof p.icon === 'string') return p.icon
    if (p.icon.src) return String(p.icon.src)
  }

  if (p.image) return String(p.image)

  return null
}

function toInteger(value: any): number | null {
  if (value === null || value === undefined || value === '') return null
  const n = Number(value)
  return Number.isInteger(n) ? n : null
}

function formatXMLTVDate(date: any): string {
  return dayjs(date).utc().format('YYYYMMDDHHmmss ZZ')
}

function normalizeYear(value: any): string | null {
  if (value === null || value === undefined || value === '') return null
  const match = String(value).match(/\b(19\d{2}|20\d{2})\b/)
  return match ? match[1] : null
}

function escapeXml(value: string): string {
  return String(value)
    .replace(/&/g, '&amp;')
    .replace(/</g, '&lt;')
    .replace(/>/g, '&gt;')
    .replace(/"/g, '&quot;')
    .replace(/'/g, '&apos;')
    .replace(/\r?\n|\r/g, ' ')
    .replace(/\s+/g, ' ')
    .trim()
}
