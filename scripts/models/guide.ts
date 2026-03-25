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

  const channelId = escapeXml(p.channel || '')
  const start = formatXMLTVDate(p.start)
  const stop = formatXMLTVDate(p.stop)

  let output = `  <programme start="${start}" stop="${stop}" channel="${channelId}">\n`

  // 1) titles
  if (Array.isArray(p.titles) && p.titles.length) {
    p.titles.forEach((title: { value?: string; lang?: string }) => {
      if (!title?.value) return

      const langAttr = title.lang ? ` lang="${escapeXml(title.lang)}"` : ''
      output += `    <title${langAttr}>${escapeXml(title.value)}</title>\n`
    })
  } else if (p.title_es || p.title) {
    const titleEs = p.title_es || p.title
    output += `    <title lang="es">${escapeXml(String(titleEs))}</title>\n`

    if (p.title_en) {
      output += `    <title lang="en">${escapeXml(String(p.title_en))}</title>\n`
    }
  }

  // 2) desc
  const description = p.description || p.synopsis || p.desc
  if (description) {
    output += `    <desc lang="es">${escapeXml(String(description))}</desc>\n`
  }

  // 3) sub-title
  const subtitle = p.sub_title || p.subtitle || p.episode_title
  if (subtitle) {
    output += `    <sub-title lang="es">${escapeXml(String(subtitle))}</sub-title>\n`
  }

  // 4) category
  if (p.category) {
    output += `    <category lang="es">${escapeXml(String(p.category))}</category>\n`
  }

  // 5) date
  const year = normalizeYear(p.year || p.date)
  if (year) {
    output += `    <date>${year}</date>\n`
  }

  // 6) episode-num
  const season = toInteger(p.season)
  const episode = toInteger(p.episode)

  if (season !== null && episode !== null) {
    output += `    <episode-num system="xmltv_ns">${season - 1}.${episode - 1}.0/1</episode-num>\n`
    output += `    <episode-num system="onscreen">S${String(season).padStart(2, '0')}E${String(episode).padStart(2, '0')}</episode-num>\n`
  } else if (episode !== null) {
    output += `    <episode-num system="onscreen">E${String(episode).padStart(2, '0')}</episode-num>\n`
  }

  // 7) rating
  if (p.rating !== null && p.rating !== undefined && p.rating !== '') {
    output += `    <rating><value>${escapeXml(String(p.rating))}</value></rating>\n`
  }

  // 8) image
  if (p.image) {
    output += `    <image>${escapeXml(String(p.image))}</image>\n`
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
