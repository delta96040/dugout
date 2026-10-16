import ClubPicker from "@/components/ClubPicker";
import { chapterContent } from "@/components/story/Chapters";
import StaticPitch from "@/components/story/StaticPitch";
import Story from "@/components/story/Story";
import { clubSlugs, dossier } from "@/lib/data";
import { storyData } from "@/lib/story";

/** One club's scroll story (server-rendered chapters, client shell), with the club picker above it. */
export default function ClubStory({ slug }: { slug: string }) {
  const data = storyData(dossier(slug));
  const clubs = clubSlugs().map((s) => ({ slug: s, name: dossier(s).club.name }));
  return (
    <>
      <div className="grid12">
        <ClubPicker clubs={clubs} current={slug} />
      </div>
      <Story scene={data.scene} pitch={<StaticPitch data={data} />} chapters={chapterContent(data)} />
    </>
  );
}
