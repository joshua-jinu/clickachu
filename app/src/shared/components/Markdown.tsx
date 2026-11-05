/** Streamed Markdown answer (§9.2.4) with highlighted code and a copy button per block. */
import { type ComponentPropsWithoutRef, memo, useRef } from 'react'
import ReactMarkdown, { type ExtraProps, type Options } from 'react-markdown'
import rehypeHighlight from 'rehype-highlight'
import remarkGfm from 'remark-gfm'
import { stripVoiceTags } from '../voiceSpeech'
import { CopyButton } from './CopyButton'

function CodeBlock(props: ComponentPropsWithoutRef<'pre'> & ExtraProps) {
  const { node, ...preProps } = props
  void node // hast node; not a DOM attribute
  const ref = useRef<HTMLPreElement>(null)
  return (
    <div className="ck-code group relative">
      <pre ref={ref} {...preProps} />
      <CopyButton
        getText={() => ref.current?.innerText ?? ''}
        className="absolute top-1.5 right-1.5 opacity-0 group-hover:opacity-100 focus-visible:opacity-100"
      />
    </div>
  )
}

function ScrollableTable({ node, ...props }: ComponentPropsWithoutRef<'table'> & ExtraProps) {
  void node
  return <div className="max-w-full overflow-x-auto"><table {...props} /></div>
}

/** Links open in the system browser (main process routes window.open to shell.openExternal). */
function ExternalLink({ node, ...props }: ComponentPropsWithoutRef<'a'> & ExtraProps) {
  void node
  return <a {...props} target="_blank" rel="noreferrer" />
}

const components = { pre: CodeBlock, table: ScrollableTable, a: ExternalLink }
const rehypePlugins: Options['rehypePlugins'] = [[rehypeHighlight, { detect: true }]]

function extractThoughts(raw: string): { thoughts: string[]; content: string; isThinking: boolean } {
  const thoughts: string[] = []
  let isThinking = false

  // Replace complete <thought>...</thought> or <think>...</think>
  let content = raw.replace(/<(?:thought|think)>([\s\S]*?)<\/(?:thought|think)>/gi, (_, thought) => {
    const trimmed = thought.trim()
    if (trimmed) thoughts.push(trimmed)
    return ''
  })

  // Check for currently unclosed streaming thought: <thought>...
  const openMatch = content.match(/<(?:thought|think)>([\s\S]*)$/i)
  if (openMatch) {
    isThinking = true
    const currentThought = openMatch[1].trim()
    if (currentThought) thoughts.push(currentThought)
    content = content.slice(0, openMatch.index)
  }

  return { thoughts, content: stripVoiceTags(content.trim()), isThinking }
}

export const Markdown = memo(function Markdown({ text }: { text: string }) {
  const { thoughts, content, isThinking } = extractThoughts(text)

  return (
    <div className="ck-markdown space-y-2">
      {thoughts.map((th, idx) => (
        <details
          key={idx}
          open={isThinking && idx === thoughts.length - 1}
          className="group rounded-md border border-white/10 bg-white/[0.03] p-2 text-xs transition-colors hover:border-white/20"
        >
          <summary className="cursor-pointer font-medium text-ck-muted select-none flex items-center gap-1.5 hover:text-ck-text">
            <span>💭</span>
            <span>
              {isThinking && idx === thoughts.length - 1
                ? 'Thinking & Planning…'
                : 'Thought process & Plan'}
            </span>
          </summary>
          <div className="mt-2 whitespace-pre-wrap pl-3.5 border-l border-ck-violet/40 text-[11px] font-mono leading-relaxed text-ck-text/80">
            {th}
          </div>
        </details>
      ))}

      {content ? (
        <ReactMarkdown components={components} rehypePlugins={rehypePlugins} remarkPlugins={[remarkGfm]}>
          {content}
        </ReactMarkdown>
      ) : null}
    </div>
  )
})
