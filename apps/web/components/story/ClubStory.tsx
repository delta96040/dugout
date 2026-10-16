import ClubPicker from "@/components/ClubPicker";
import { chapterContent } from "@/components/story/Chapters";
import StaticPitch from "@/components/story/StaticPitch";
import Story from "@/components/story/Story";
import { dossier, leagues } from "@/lib/data";
import { storyData } from "@/lib/story";

/** One club's scroll story (server-rendered chapters, client shell), with the club picker above it. */
export default function ClubStory({ slug }: { slug: string }) {
  const data = storyData(dossier(slug));
  const groups = leagues().map((l) => ({
    league: l.name,
    clubs: l.clubs.map((d) => ({ slug: d.club.slug, name: d.club.name })).sort((a, b) => a.name.localeCompare(b.name)),
  }));
  return (
    <>
      <div className="grid12">
        <ClubPicker groups={groups} current={slug} />
      </div>
      <Story scene={data.scene} pitch={<StaticPitch data={data} />} chapters={chapterContent(data)} />
    </>
  );
}
